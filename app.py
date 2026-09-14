# 執行方式（在程式資料夾下）：
#   python app.py               只有 Stroop（目前 REC 核准的版本，預設）
#   python app.py --with-2back  Stroop + 2-back（REC 變更案核准後才能使用）
# 啟動後畫面會直接印出主控端與學生端的網址，以及目前是哪一種版本。
#
# 只有 Stroop 的流程：
#   主控端選「當日場次」→ 按開始
#   → 學生做 Stroop（8 題練習 + 24 題正式）
#   → 顯示個人成績與排名，測驗結束
#
# Stroop + 2-back 的流程：
#   主控端選「施測類型」與「當日場次」→ 按開始
#   → 學生做 Stroop（8 題練習 + 24 題正式）
#   → 顯示個人成績與排名，倒數後自動進入 2-back 說明頁
#   → 2-back（第一次施測 12 題練習／其後 6 題暖身，接 30 題正式）
#   → 顯示「完成」，不顯示任何成績
#
# 資料落檔：第一筆資料進來時才建立 CSV，之後逐筆附加寫入，
#          手機斷線或伺服器重啟都不影響已寫入的資料。

import os
import csv
import argparse
import socket
import uuid
import warnings
from datetime import datetime, timezone

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

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
PORT = 5000

# 是否啟用 2-back 工作記憶測驗。
# 同意書與目前核准的計畫書只包含 Stroop，REC 變更案通過之前一律不能施測 2-back，
# 因此預設關閉，必須在啟動時明確加上 --with-2back 才會打開。
# 刻意不做成主控端網頁上的選項：網頁選項容易在施測現場被誤點，
# 啟動參數則要重開伺服器才能改變，也會清楚印在啟動訊息與主控端頁面上。
TWOBACK_ENABLED = False

TEST_SET_STROOP_ONLY = 'Stroop'
TEST_SET_WITH_TWOBACK = 'Stroop+2-back'

STROOP_HEADER = [
    "Session_ID", "Session_Slot", "Test_Set", "Practice_Type", "Student_ID", "Attempt",
    "Trial", "Condition", "Stimulus_Onset", "Reaction_Time_ms", "Is_Correct",
    "Page_Hidden",
]

TWOBACK_HEADER = [
    "Session_ID", "Session_Slot", "Practice_Type", "Student_ID", "Attempt",
    "Phase", "Trial", "Letter", "Is_Target", "Responded", "Response_Type",
    "Is_Correct", "Reaction_Time_ms", "Stimulus_Onset", "Page_Hidden",
]

connected_users = {}
test_results = []
completed_stats = {}

# 本場施測的設定與檔案路徑；未開始時為 None
session_info = None

# 每位學生的 2-back 序列，key 為 student_id，
# 以 student_id 而非 sid 為 key，手機重新整理後仍拿到同一組序列。
twoback_sequences = {}

# 本場每位學生各自做過幾次 Stroop／2-back。
# 手機重新整理或斷線重連會讓同一個人在同一場重跑一次，
# 這時不刪掉舊資料，而是把新的那一輪標成第 2 次嘗試（Attempt 欄），
# 分析時取最後一次完整的即可；兩輪都留著才看得出現場發生過什麼事。
stroop_attempts = {}
twoback_attempts = {}

# 主控端的連線。學生名單與「退回重新輸入」只對這些連線開放：
# 名單裡有全班的座號，不應該廣播到每一支學生手機上。
ADMIN_ROOM = 'admins'
admin_sids = set()

# 每次啟動伺服器時產生一個新的編號，寫進學生端頁面。
# 伺服器重開後，還開著舊頁面的手機會自動重連；編號不同就表示伺服器重開過
# （場次設定已經不在了），這時要讓手機回到輸入座號的畫面，
# 而不是悄悄重新出現在主控端的名單上。
SERVER_BOOT_ID = uuid.uuid4().hex

# 被主控端退回的頁面識別碼。手機若在退回的當下剛好斷線或正在重連，
# 退回的訊息會送不到；等它重新連上時，伺服器認出這個識別碼就再退回一次。
kicked_tokens = set()


def broadcast_users():
    """把連線名單推給主控端，並標出重複的座號。

    兩位學生打錯成同一個號碼、或同一個人用兩支裝置登入時，
    兩邊的資料會混在同一個 Student_ID 底下而無法分辨。
    在主控端把重複的標出來，研究人員按下開始之前就能發現。
    """
    counts = {}
    for user in connected_users.values():
        sid = user.get('student_id')
        counts[sid] = counts.get(sid, 0) + 1

    user_list = []
    for sid, user in connected_users.items():
        item = dict(user)
        item.pop('token', None)
        item['sid'] = sid    # 主控端按「退回重新輸入」時用來指定是哪一支手機
        item['duplicate'] = counts.get(user.get('student_id'), 0) > 1
        user_list.append(item)

    # 只發給主控端
    emit('update_users', {
        'users': user_list,
        'duplicate_ids': sorted(s for s, n in counts.items() if n > 1),
    }, to=ADMIN_ROOM)


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
        'twoback_enabled': TWOBACK_ENABLED,
    }


def session_info_payload():
    """發給主控端的本場資訊。"""
    return {
        'session_id': session_info['session_id'],
        'test_set': session_info['test_set'],
        'twoback_enabled': TWOBACK_ENABLED,
        'practice_type': session_info['practice_type'],
        'session_slot': session_info['session_slot'],
        'stroop_csv': session_info['stroop_csv'],
        'twoback_csv': session_info['twoback_csv'],
    }


def as_dict(data):
    """把收到的東西保證變成 dict。

    正常的手機端一定送 dict，但只要有人用瀏覽器主控台亂送，
    或前端在奇怪的狀態下送出半成品，handler 就會拋例外。
    例外雖然會被 socketio 接住、伺服器不會掛掉，
    但那位學生這一題（甚至整份成績）就會安靜地沒被寫進檔案，
    現場完全看不出來，所以一律先過這一關。
    """
    return data if isinstance(data, dict) else {}


def csv_safe(value):
    """讓文字欄位在 Excel 裡不會被當成公式。

    Excel 會把開頭是 = + - @ 的儲存格當公式執行。座號理論上都是數字，
    但只要有人輸入 -11459 這種東西，研究人員用 Excel 打開 CSV 時
    就會看到錯誤或被執行的內容。這裡在前面補一個單引號讓它保持純文字。
    """
    if value is None:
        return ''
    text = str(value)
    if text[:1] in ('=', '+', '-', '@'):
        return "'" + text
    return text


def clean_student_id(value):
    """整理座號：去掉前後空白，並限制長度避免超長字串塞爆檔案。"""
    if value is None:
        return '未知'
    text = str(value).strip()
    if not text:
        return '未知'
    return text[:32]


def clean_token(value):
    """學生端頁面識別碼：每次載入頁面隨機產生，用來認出「同一個頁面」的重連。"""
    if not isinstance(value, str):
        return ''
    return value[:64]


def yes_no(value):
    """把前端送來的旗標統一成「是／否」。"""
    if value is True or value in ('是', 'true', True):
        return '是'
    if value is False or value in ('否', 'false', False):
        return '否'
    return ''


def append_csv(path, header, row):
    """逐筆附加寫入並立即落檔，檔案不存在時先補上欄位列。

    刻意不在開場時就把檔案建出來：研究人員若不小心按到開始又馬上結束，
    output 資料夾就會留下一堆空檔，正式收資料時很難認哪個才是真的。
    改成第一筆資料進來時才建檔，沒有資料的場次就不會留下檔案。

    每次都重新開檔關檔，確保作業系統層面確實寫出，
    伺服器若中途被關掉也不會掉已經寫進去的資料。
    """
    is_new = not os.path.exists(path)
    with open(path, mode='a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(header)
        writer.writerow(row)


@app.route('/')
def index():
    return render_template('client.html', twoback_enabled=TWOBACK_ENABLED,
                           server_boot_id=SERVER_BOOT_ID)


@app.route('/admin')
def admin():
    # 主控端的 QR code 要編進「學生端網址」。
    # 不能直接用瀏覽器網址列的主機名稱：研究人員若用 localhost 開主控端，
    # QR code 就會變成 localhost，學生手機掃了連不上。
    # 所以由伺服器偵測區域網路 IP，每次開頁面都重新偵測，換教室、換 IP 也不用改。
    ip = primary_lan_ip()
    student_url = 'http://%s:%d' % (ip, PORT) if ip else ''
    other_urls = ['http://%s:%d' % (x, PORT) for x in all_lan_ips() if x != ip]
    return render_template('admin.html', twoback_enabled=TWOBACK_ENABLED,
                           student_url=student_url, other_urls=other_urls)


@socketio.on('join')
def handle_join(data):
    data = as_dict(data)
    student_id = clean_student_id(data.get('student_id'))

    status = 'Stroop 測驗中' if session_info else '等待中'
    connected_users[request.sid] = {'student_id': student_id, 'status': status,
                                    'token': clean_token(data.get('token'))}
    broadcast_users()

    # 中途加入或手機重新整理時，直接讓他接上進行中的場次
    if session_info:
        emit('test_started', session_payload(), to=request.sid)


@socketio.on('rejoin')
def handle_rejoin(data):
    """Wi-Fi 斷一下又接回來時，重新登記這條連線是誰。

    socket.io 重連後會拿到全新的連線編號，伺服器若不重新對應，
    這位學生接下來送回的每一題都會變成「未知」，資料等於白收。
    這裡刻意不發 test_started：手機上的測驗其實一直在跑，
    把畫面重設回 Stroop 反而會毀掉正在進行的那一場。
    """
    data = as_dict(data)

    # 伺服器重開過：舊場次已經不在，請手機回到輸入座號的畫面重新加入
    if data.get('boot_id') != SERVER_BOOT_ID:
        emit('server_restarted', {}, to=request.sid)
        return

    token = clean_token(data.get('token'))

    # 這個頁面先前已被主控端退回，只是當時沒收到訊息：再退回一次
    if token and token in kicked_tokens:
        emit('kicked', {}, to=request.sid)
        return

    student_id = clean_student_id(data.get('student_id'))
    status = data.get('status')

    if not isinstance(status, str) or not status:
        status = 'Stroop 測驗中' if session_info else '等待中'

    connected_users[request.sid] = {'student_id': student_id, 'status': status,
                                    'token': token}
    broadcast_users()


@socketio.on('disconnect')
def handle_disconnect():
    admin_sids.discard(request.sid)
    if request.sid in connected_users:
        del connected_users[request.sid]
        broadcast_users()


@socketio.on('kick_user')
def handle_kick_user(data):
    """主控端把某位學生退回輸入座號的畫面（例如座號打錯）。

    只接受主控端連線送來的請求。被退回的手機會重新整理回到登入畫面，
    伺服器同時把他從名單移除。已經寫進 CSV 的資料不會刪除，
    仍留在原本（打錯的）座號底下，分析時需要自行排除。
    """
    if request.sid not in admin_sids:
        return
    target = as_dict(data).get('sid')
    if target not in connected_users:
        return

    token = connected_users[target].get('token')
    if token:
        # 記住這個頁面：若它此刻剛好斷線沒收到退回訊息，重連時會再被退回一次
        kicked_tokens.add(token)
        # 同一個頁面若因斷線重連而在名單上留下舊連線，一併移除
        sids = [s for s, u in connected_users.items() if u.get('token') == token]
    else:
        sids = [target]

    for s in sids:
        emit('kicked', {}, to=s)
        connected_users.pop(s, None)
    broadcast_users()


@socketio.on('start_test')
def handle_start(data=None):
    global test_results, completed_stats, session_info, twoback_sequences

    if session_info:
        return

    data = as_dict(data)

    if TWOBACK_ENABLED:
        practice_type = data.get('practice_type', 'regular')
        if practice_type not in ('first', 'regular'):
            practice_type = 'regular'
    else:
        # 只有 Stroop 時沒有練習／暖身之分，這一欄寫明「不適用」，
        # 避免之後合併資料時被誤以為是 2-back 的一般施測。
        practice_type = '不適用'

    try:
        session_slot = int(data.get('session_slot', 1))
    except (TypeError, ValueError):
        session_slot = 1
    if session_slot not in (1, 2):
        session_slot = 1

    test_results = []
    completed_stats = {}
    twoback_sequences = {}
    stroop_attempts.clear()
    twoback_attempts.clear()

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    stroop_csv = os.path.join(OUTPUT_DIR, "stroop_%s.csv" % session_id)
    twoback_csv = (os.path.join(OUTPUT_DIR, "twoback_%s.csv" % session_id)
                   if TWOBACK_ENABLED else None)

    session_info = {
        'session_id': session_id,
        'test_set': TEST_SET_WITH_TWOBACK if TWOBACK_ENABLED else TEST_SET_STROOP_ONLY,
        'practice_type': practice_type,
        'session_slot': session_slot,
        'stroop_csv': stroop_csv,
        'twoback_csv': twoback_csv,
    }

    for sid in connected_users:
        connected_users[sid]['status'] = 'Stroop 測驗中'
    broadcast_users()

    emit('session_info', session_info_payload(), broadcast=True)

    emit('test_started', session_payload(), broadcast=True)


@socketio.on('submit_result')
def handle_submit(data):
    """Stroop 正式題作答完畢，整包送回。"""
    results = as_dict(data).get('results', [])
    if not isinstance(results, list):
        results = []
    results = [r for r in results if isinstance(r, dict)]

    student_id = connected_users.get(request.sid, {}).get('student_id', '未知')

    # 手機重新整理或斷線重連會讓同一個人再跑一次，
    # 舊的那一輪留著不動，新的這一輪標成第 2 次嘗試。
    attempt = stroop_attempts.get(student_id, 0) + 1
    stroop_attempts[student_id] = attempt

    test_results.extend(results)

    if session_info:
        for r in results:
            append_csv(session_info['stroop_csv'], STROOP_HEADER, [
                session_info['session_id'],
                session_info['session_slot'],
                session_info['test_set'],
                session_info['practice_type'],
                csv_safe(clean_student_id(r.get('student_id', student_id))),
                attempt,
                r.get('trial'),
                r.get('condition'),
                iso_from_ms(r.get('onset_ms')),
                r.get('rt'),
                r.get('correct'),
                yes_no(r.get('page_hidden')),
            ])

    total_trials = len(results)
    correct_trials = [r for r in results if r.get('correct') == '是']

    accuracy = (len(correct_trials) / total_trials * 100) if total_trials > 0 else 0

    rts = [r.get('rt') for r in correct_trials]
    rts = [x for x in rts if isinstance(x, (int, float))]
    if rts:
        avg_rt = sum(rts) / len(rts)
    else:
        avg_rt = 999999

    completed_stats[request.sid] = {
        'student_id': student_id,
        'accuracy': accuracy,
        'avg_rt': avg_rt
    }

    if request.sid in connected_users:
        # 只有 Stroop 時，交完 Stroop 就是整場結束
        connected_users[request.sid]['status'] = 'Stroop 已完成' if TWOBACK_ENABLED else '已完成'
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
    # 未啟用 2-back 時伺服器一律拒絕，就算學生端被竄改也拿不到題目。
    if not session_info or not TWOBACK_ENABLED:
        return

    student_id = connected_users.get(request.sid, {}).get('student_id', '未知')

    twoback_attempts[student_id] = twoback_attempts.get(student_id, 0) + 1

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
    # 未啟用 2-back 時不收任何 2-back 資料，也不會建立 2-back 的 CSV。
    if not session_info or not TWOBACK_ENABLED:
        return

    data = as_dict(data)
    student_id = connected_users.get(request.sid, {}).get('student_id', '未知')

    append_csv(session_info['twoback_csv'], TWOBACK_HEADER, [
        session_info['session_id'],
        session_info['session_slot'],
        session_info['practice_type'],
        csv_safe(student_id),
        twoback_attempts.get(student_id, 1),
        data.get('phase'),
        data.get('trial'),
        data.get('letter'),
        data.get('is_target'),
        data.get('responded'),
        data.get('response_type'),
        data.get('is_correct'),
        data.get('rt'),
        iso_from_ms(data.get('onset_ms')),
        yes_no(data.get('page_hidden')),
    ])


@socketio.on('twoback_done')
def handle_twoback_done():
    if not TWOBACK_ENABLED:
        return
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
        'client_sent_ms': as_dict(data).get('client_sent_ms'),
        'server_time_ms': server_time_ms(),
    }, to=request.sid)


@socketio.on('request_status')
def handle_request_status():
    """主控端連上（或重新整理）時呼叫：登記為主控端，並取回目前場次狀態。

    學生名單與「退回重新輸入」只開放給登記過的主控端連線。
    """
    admin_sids.add(request.sid)
    join_room(ADMIN_ROOM)
    if session_info:
        emit('session_info', session_info_payload(), to=request.sid)
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
    if TWOBACK_ENABLED:
        print('  測驗版本：Stroop + 2-back 工作記憶測驗')
        print('  ※ 僅限 REC 變更案核准後使用')
    else:
        print('  測驗版本：只有 Stroop（目前核准的版本）')
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
    parser = argparse.ArgumentParser(description='認知測驗伺服器')
    parser.add_argument(
        '--with-2back', action='store_true',
        help='加入 2-back 工作記憶測驗（僅限 REC 變更案核准後使用）')
    args = parser.parse_args()
    TWOBACK_ENABLED = args.with_2back

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
