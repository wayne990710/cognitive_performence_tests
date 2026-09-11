# 執行方式：在 stroop_project 資料夾下輸入 python app.py
# 啟動後畫面會直接印出主控端與學生端的網址，不需要自己去查 IP。
#
# 一場施測的流程：
#   主控端選「施測類型」與「當日場次」→ 按開始
#   → 學生做 Stroop（8 題練習 + 24 題正式）
#   → 顯示個人成績與排名，倒數後自動進入 2-back 說明頁
#   → 2-back（第一次施測 12 題練習／其後 6 題暖身，接 30 題正式）
#   → 顯示「完成」，不顯示任何成績
#
# 資料落檔：開始測驗時即建立兩個 CSV，之後逐筆附加寫入，
#          手機斷線或伺服器重啟都不影響已寫入的資料。

import os
import csv
import socket
import warnings
from datetime import datetime, timezone

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

import twoback_sequence as tb

app = Flask(__name__)
app.config['SECRET_KEY'] = 'kahoot_stroop_secret'
# 不開 debug 時 Flask 預設會快取樣板，改完 client.html 不重啟就不會生效。
app.config['TEMPLATES_AUTO_RELOAD'] = True
# eventlet 會在這一行被載入時印一段很長的淘汰警告，蓋掉啟動訊息裡的網址。
# 這裡只是把那段訊息藏起來，功能完全不受影響。
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    socketio = SocketIO(app)

OUTPUT_DIR = 'output'

STROOP_HEADER = [
    "Session_ID", "Session_Slot", "Practice_Type", "Student_ID",
    "Trial", "Condition", "Stimulus_Onset", "Reaction_Time_ms", "Is_Correct",
]

TWOBACK_HEADER = [
    "Session_ID", "Session_Slot", "Practice_Type", "Student_ID",
    "Phase", "Trial", "Letter", "Is_Target", "Responded", "Response_Type",
    "Is_Correct", "Reaction_Time_ms", "Stimulus_Onset",
]

connected_users = {}
test_results = []
completed_stats = {}

# 本場施測的設定與檔案路徑；未開始時為 None
session_info = None

# 每位學生的 2-back 序列，key 為 student_id，
# 以 student_id 而非 sid 為 key，手機重新整理後仍拿到同一組序列。
twoback_sequences = {}

# 本場已經交過 Stroop 成績的學生，避免斷線重連後重跑一次造成資料重複
stroop_submitted = set()


def broadcast_users():
    user_list = list(connected_users.values())
    emit('update_users', {'users': user_list}, broadcast=True)


def server_time_ms():
    """伺服器目前時間（epoch 毫秒），供各手機校正自身時鐘。"""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso_from_ms(ms):
    """epoch 毫秒 → ISO 8601 含毫秒的本地時間字串，例如 2026-09-17T14:23:05.478。

    由伺服器統一轉換，不讓各支手機自己格式化，
    避免某支手機時區設定錯誤導致時間戳記對不上 AirBox 的時間軸。
    """
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def session_payload():
    """發給學生端的本場設定。"""
    if session_info is None:
        return None
    return {
        'server_time_ms': server_time_ms(),
        'practice_type': session_info['practice_type'],
        'session_slot': session_info['session_slot'],
    }


def create_csv(path, header):
    with open(path, mode='w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerow(header)


def append_csv(path, row):
    """逐筆附加寫入並立即落檔。

    每次都重新開檔關檔，確保作業系統層面確實寫出，
    伺服器若中途被關掉也不會掉資料。
    """
    with open(path, mode='a', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerow(row)


@app.route('/')
def index():
    return render_template('client.html')


@app.route('/admin')
def admin():
    return render_template('admin.html')


@socketio.on('join')
def handle_join(data):
    student_id = data.get('student_id')

    status = '測驗中' if session_info else '等待中'
    connected_users[request.sid] = {'student_id': student_id, 'status': status}
    broadcast_users()

    # 中途加入或手機重新整理時，直接讓他接上進行中的場次
    if session_info:
        emit('test_started', session_payload(), to=request.sid)


@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in connected_users:
        del connected_users[request.sid]
        broadcast_users()


@socketio.on('start_test')
def handle_start(data=None):
    global test_results, completed_stats, session_info, twoback_sequences

    if session_info:
        return

    data = data or {}

    practice_type = data.get('practice_type', 'regular')
    if practice_type not in ('first', 'regular'):
        practice_type = 'regular'

    try:
        session_slot = int(data.get('session_slot', 1))
    except (TypeError, ValueError):
        session_slot = 1
    if session_slot not in (1, 2):
        session_slot = 1

    test_results = []
    completed_stats = {}
    twoback_sequences = {}
    stroop_submitted.clear()

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    stroop_csv = os.path.join(OUTPUT_DIR, "stroop_%s.csv" % session_id)
    twoback_csv = os.path.join(OUTPUT_DIR, "twoback_%s.csv" % session_id)

    create_csv(stroop_csv, STROOP_HEADER)
    create_csv(twoback_csv, TWOBACK_HEADER)

    session_info = {
        'session_id': session_id,
        'practice_type': practice_type,
        'session_slot': session_slot,
        'stroop_csv': stroop_csv,
        'twoback_csv': twoback_csv,
    }

    for sid in connected_users:
        connected_users[sid]['status'] = 'Stroop 測驗中'
    broadcast_users()

    emit('session_info', {
        'session_id': session_id,
        'practice_type': practice_type,
        'session_slot': session_slot,
        'stroop_csv': stroop_csv,
        'twoback_csv': twoback_csv,
    }, broadcast=True)

    emit('test_started', session_payload(), broadcast=True)


@socketio.on('submit_result')
def handle_submit(data):
    """Stroop 正式題作答完畢，整包送回。"""
    results = data.get('results', [])
    student_id = connected_users.get(request.sid, {}).get('student_id', '未知')

    # 手機斷線重連會從頭再跑一次 Stroop，同一場只採計第一次送回的成績，
    # 否則 CSV 裡會出現同一位學生同一題號的兩筆資料。
    if student_id in stroop_submitted:
        return
    stroop_submitted.add(student_id)

    test_results.extend(results)

    if session_info:
        for r in results:
            append_csv(session_info['stroop_csv'], [
                session_info['session_id'],
                session_info['session_slot'],
                session_info['practice_type'],
                r.get('student_id', student_id),
                r.get('trial'),
                r.get('condition'),
                iso_from_ms(r.get('onset_ms')),
                r.get('rt'),
                r.get('correct'),
            ])

    total_trials = len(results)
    correct_trials = [r for r in results if r['correct'] == '是']

    accuracy = (len(correct_trials) / total_trials * 100) if total_trials > 0 else 0

    if correct_trials:
        avg_rt = sum(r['rt'] for r in correct_trials) / len(correct_trials)
    else:
        avg_rt = 999999

    completed_stats[request.sid] = {
        'student_id': student_id,
        'accuracy': accuracy,
        'avg_rt': avg_rt
    }

    if request.sid in connected_users:
        connected_users[request.sid]['status'] = 'Stroop 已完成'
        broadcast_users()

    sorted_sids = sorted(completed_stats.keys(),
                         key=lambda sid: completed_stats[sid]['avg_rt'])
    total_completed = len(sorted_sids)

    for index, sid in enumerate(sorted_sids):
        rank_data = {
            'accuracy': completed_stats[sid]['accuracy'],
            'avg_rt': completed_stats[sid]['avg_rt'],
            'rank': index + 1,
            'total': total_completed
        }
        emit('personal_rank', rank_data, to=sid)


@socketio.on('request_twoback')
def handle_request_twoback():
    """學生進入 2-back 說明頁時索取自己的序列。

    序列在伺服器端生成，每位學生各自獨立，避免鄰座互相對照。
    """
    if not session_info:
        return

    student_id = connected_users.get(request.sid, {}).get('student_id', '未知')

    if student_id not in twoback_sequences:
        twoback_sequences[student_id] = {
            'practice': tb.generate_practice_sequence(
                session_info['practice_type']),
            'formal': tb.generate_formal_sequence(),
        }

    sequences = twoback_sequences[student_id]

    if request.sid in connected_users:
        connected_users[request.sid]['status'] = '記憶測驗中'
        broadcast_users()

    emit('twoback_sequence', {
        'practice': sequences['practice'],
        'formal': sequences['formal'],
        'practice_type': session_info['practice_type'],
        'server_time_ms': server_time_ms(),
    }, to=request.sid)


@socketio.on('twoback_trial')
def handle_twoback_trial(data):
    """2-back 每答完一題就回傳一筆，立刻落檔。"""
    if not session_info:
        return

    student_id = connected_users.get(request.sid, {}).get('student_id', '未知')

    append_csv(session_info['twoback_csv'], [
        session_info['session_id'],
        session_info['session_slot'],
        session_info['practice_type'],
        student_id,
        data.get('phase'),
        data.get('trial'),
        data.get('letter'),
        data.get('is_target'),
        data.get('responded'),
        data.get('response_type'),
        data.get('is_correct'),
        data.get('rt'),
        iso_from_ms(data.get('onset_ms')),
    ])


@socketio.on('twoback_done')
def handle_twoback_done():
    if request.sid in connected_users:
        connected_users[request.sid]['status'] = '已完成'
        broadcast_users()


@socketio.on('end_session')
def handle_end_session():
    """結束本場，讓主控端可以開始下一場。資料已逐筆落檔，不需另外匯出。"""
    global session_info

    if not session_info:
        return

    finished = session_info
    session_info = None

    for sid in connected_users:
        connected_users[sid]['status'] = '等待中'
    broadcast_users()

    emit('session_ended', {
        'session_id': finished['session_id'],
        'stroop_csv': finished['stroop_csv'],
        'twoback_csv': finished['twoback_csv'],
    }, broadcast=True)


@socketio.on('time_sync')
def handle_time_sync(data):
    """手機時鐘校正：回傳伺服器時間，由手機自行扣掉來回延遲的一半。

    每題的時間戳記都以校正後的時間計算，全班因此共用同一條時間軸，
    後續才能與微型空氣品質感測站的 CO2 曲線對齊。
    """
    emit('time_sync_reply', {
        'client_sent_ms': data.get('client_sent_ms'),
        'server_time_ms': server_time_ms(),
    }, to=request.sid)


@socketio.on('request_status')
def handle_request_status():
    """主控端重新整理後，取回目前場次狀態。"""
    if session_info:
        emit('session_info', {
            'session_id': session_info['session_id'],
            'practice_type': session_info['practice_type'],
            'session_slot': session_info['session_slot'],
            'stroop_csv': session_info['stroop_csv'],
            'twoback_csv': session_info['twoback_csv'],
        }, to=request.sid)
    broadcast_users()


def primary_lan_ip():
    """本機在區域網路上的 IPv4，也就是學生手機要連的那個位址。

    作法是開一個 UDP socket 去「連」一個外部位址再問作業系統用了哪張網卡。
    UDP 的 connect 不會真的送出任何封包，所以沒有網際網路也能問出答案。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def all_lan_ips():
    """本機所有的 IPv4，用來提示還有哪些位址可以試。

    筆電常常同時有 Wi-Fi、有線網路、VPN 或虛擬機的網卡，
    自動選到的那個不一定是教室 Wi-Fi 的那張。
    """
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith('127.'):
                ips.append(ip)
    except socket.gaierror:
        pass
    return ips


def port_in_use(port):
    """檢查 port 是否已經被占用。

    最常見的情況是研究人員忘記前一個視窗還開著又按了一次執行，
    這時候直接看到一長串錯誤訊息會很難判斷發生什麼事。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(('127.0.0.1', port)) == 0
    finally:
        s.close()


def print_startup_banner(port):
    ip = primary_lan_ip()
    others = [x for x in all_lan_ips() if x != ip]

    line = '=' * 56

    print()
    print(line)
    print('  認知測驗伺服器已啟動')
    print(line)

    if ip:
        print()
        print('  學生端（請學生用手機瀏覽器開啟）')
        print('      http://%s:%d' % (ip, port))
        print()
        print('  主控端（研究人員自己開）')
        print('      http://%s:%d/admin' % (ip, port))
    else:
        print()
        print('  找不到區域網路位址，請確認電腦有連上教室的 Wi-Fi。')

    if others:
        print()
        print('  這台電腦還有其他網路位址，上面的連不到時可以改試：')
        for other in others:
            print('      http://%s:%d' % (other, port))

    print()
    print('  手機必須和這台電腦連同一個 Wi-Fi。')
    print('  要關閉伺服器請按 Ctrl + C。')
    print(line)
    print()


if __name__ == '__main__':
    PORT = 5000

    if port_in_use(PORT):
        print()
        print('=' * 56)
        print('  啟動失敗：連接埠 %d 已經被占用。' % PORT)
        print('=' * 56)
        print()
        print('  最可能的原因是這支程式已經在另一個視窗執行中。')
        print('  請找到那個視窗（標題通常是 python app.py）繼續使用，')
        print('  或在該視窗按 Ctrl + C 關掉之後再重新執行一次。')
        print()
        raise SystemExit(1)

    print_startup_banner(PORT)
    # 正式施測時不開 debug：自動重載會在測驗中途踢掉所有手機的連線。
    socketio.run(app, host='0.0.0.0', port=PORT, debug=False)
