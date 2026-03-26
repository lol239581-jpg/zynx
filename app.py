# gevent monkey-patch MUST be the very first thing
# иначе будет "maximum recursion depth exceeded"
try:
    from gevent import monkey
    monkey.patch_all()
    _ASYNC_MODE = 'gevent'
except ImportError:
    _ASYNC_MODE = 'threading'

from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
import random, string, re, os, time, hashlib, uuid, urllib.request, urllib.error, urllib.parse
import json as _json
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME',''),
    api_key=os.environ.get('CLOUDINARY_API_KEY',''),
    api_secret=os.environ.get('CLOUDINARY_API_KEY_SECRET',''),
    secure=True
)

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = "zynx-secret-key-2026-xK9mPqL3vNcR7"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=_ASYNC_MODE)

SMTP_USER = "zynx.messanger@gmail.com"

AVATAR_COLORS = ['#7c5cfc','#fc5cbc','#f59e0b','#10b981','#3b82f6','#ef4444','#8b5cf6','#06b6d4']
AVATAR_EMOJIS = ['🎮','👾','🔥','⚡','🦊','🐺','🐉','👻','🤖','💀','🦁','🐯']

BANNED = [
    r'бля',r'блять',r'ёб',r'еб[аоуиё]',r'[еэ]бл[аяоуи]',
    r'пизд',r'хуй',r'хуе',r'хуя',r'хуё',r'пидор',r'пидар',
    r'ёбан',r'еблан',r'сука',r'шлюх',r'мудак',r'гандон',
    r'нахуй',r'похуй',r'пиздец',r'блядь',r'бляд',r'ублюд',
    r'fuck',r'shit',r'bitch',r'asshole',r'cunt',
    r'nigger',r'nigga',r'faggot',r'whore',r'slut',r'motherfuck',
    r'pdf',r'free.*crack',r'warez',
    r'admin',r'administrator',r'moderator',r'support',r'root',r'system',r'official',
]

online = {}
pending_codes = {}

def get_db():
    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url:
        raise Exception('DATABASE_URL не задан!')
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            nickname TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            avatar_color TEXT DEFAULT '#7c5cfc',
            avatar_emoji TEXT DEFAULT '🎮',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            nickname TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS friends (
            user1 TEXT NOT NULL,
            user2 TEXT NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY (user1, user2)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            sender TEXT NOT NULL,
            receiver TEXT NOT NULL,
            text TEXT NOT NULL,
            msg_type TEXT DEFAULT 'text',
            caption TEXT DEFAULT '',
            time_ms BIGINT NOT NULL,
            deleted_for TEXT DEFAULT '',
            edited BOOLEAN DEFAULT FALSE,
            reactions TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS reactions (
            msg_id TEXT NOT NULL,
            nickname TEXT NOT NULL,
            emoji TEXT NOT NULL,
            PRIMARY KEY (msg_id, nickname)
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()

init_db()

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        if not token:
            return jsonify({'ok': False, 'error': 'Нет токена.'}), 401
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT nickname FROM tokens WHERE token=%s', (token,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return jsonify({'ok': False, 'error': 'Недействительный токен.'}), 401
        request.nickname = row['nickname']
        return f(*args, **kwargs)
    return decorated

def make_token(nickname):
    token = str(uuid.uuid4()).replace('-','') + str(uuid.uuid4()).replace('-','')
    conn = get_db()
    cur = conn.cursor()
    cur.execute('INSERT INTO tokens (token, nickname, created_at) VALUES (%s,%s,%s)', (token, nickname, time.time()))
    conn.commit(); cur.close(); conn.close()
    return token

def nick_ok(n):
    lo = n.lower()
    for p in BANNED:
        if re.search(p, lo): return False, "Никнейм содержит запрещённые слова."
    if len(n) < 3:  return False, "Никнейм слишком короткий (мин. 3)."
    if len(n) > 24: return False, "Никнейм слишком длинный (макс. 24)."
    if not re.match(r'^[a-zA-Zа-яёА-ЯЁ0-9_.\-]+$', n):
        return False, "Только буквы, цифры, _, . и -"
    return True, ""

def pass_ok(p):
    if len(p) < 8:  return False, "Пароль минимум 8 символов."
    if len(p) > 50: return False, "Пароль максимум 50 символов."
    if not re.search(r'[A-Z]', p): return False, "Нужна заглавная буква."
    if not re.search(r'[0-9]', p): return False, "Нужна цифра."
    if not re.search(r'[!@#$%^&*()\-_=+\[\]{};\':"\\|,.<>\/?`~]', p):
        return False, "Нужен спецсимвол (!@#$ и т.д.)"
    return True, ""

def email_ok(e): return bool(re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', e))
def hashpw(p):   return hashlib.sha256(p.encode()).hexdigest()
def mkcode():    return ''.join(random.choices(string.digits, k=6))

def get_profile(nick):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT avatar_color, avatar_emoji FROM users WHERE nickname=%s', (nick,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if row: return {'avatar_color': row['avatar_color'], 'avatar_emoji': row['avatar_emoji']}
    return {'avatar_color': '#7c5cfc', 'avatar_emoji': '🎮'}

def send_email(to, nickname, code):
    api_key = os.environ.get('RESEND_API_KEY', '')
    if not api_key:
        print("[EMAIL ERROR] RESEND_API_KEY не задан")
        return False
    html = f"""<html><body style="background:#07070e;font-family:sans-serif;padding:40px 20px;">
<div style="max-width:460px;margin:0 auto;background:#141420;border-radius:16px;border:1px solid #252535;overflow:hidden;">
  <div style="background:linear-gradient(135deg,#7c5cfc,#fc5cbc);padding:28px;text-align:center;">
    <h1 style="color:#fff;margin:0;letter-spacing:3px;font-size:24px;">ZYNX</h1>
  </div>
  <div style="padding:32px;">
    <p style="color:#c0c0d0;">Привет, <b style="color:#fff">{nickname}</b>!</p>
    <p style="color:#888;">Твой код подтверждения:</p>
    <div style="background:#1e1e2e;border:2px solid #7c5cfc;border-radius:12px;padding:22px;text-align:center;margin:20px 0;">
      <span style="font-size:40px;font-weight:900;letter-spacing:14px;color:#a78bfa;font-family:monospace;">{code}</span>
    </div>
    <p style="color:#666;font-size:12px;">Код действует 10 минут.</p>
  </div>
</div></body></html>"""
    payload = _json.dumps({
        "from": "Zynx <onboarding@resend.dev>",
        "to": [to],
        "subject": f"Zynx — код подтверждения: {code}",
        "html": html
    }).encode('utf-8')
    try:
        req = urllib.request.Request(
            'https://api.resend.com/emails',
            data=payload,
            headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[EMAIL OK] -> {to} ({resp.status})")
            return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False

@app.route('/')
def index(): return send_from_directory('.', 'index.html')

@app.route('/static/icon.png')
def icon():
    import base64 as _b64
    from flask import Response
    data = _b64.b64decode('/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCALjAzQDASIAAhEBAxEB/8QAHQABAAEEAwEAAAAAAAAAAAAAAAEEBQYHAgMICf/EAGAQAAEDAwEEBQkFAgoFCAYIBwEAAgMEBREGBxIhMRNBUWFxCBQiMkKBkaGxI1JiwdEVciQzQ1OCkqKywuEWNGPS8BclRFRzg4SUGGR0k6PxCSY1NjdFVWWzJ1aFlcPi/8QAGwEBAAMBAQEBAAAAAAAAAAAAAAECAwQFBgf/xABAEQACAQIDBAgEBQIFAwUBAAAAAQIDEQQhMQUSQVETImFxgZHR8DKhscEUM0JS4SPxBhUkQ4JTYnIWNESSoiX/2gAMAwEAAhEDEQA/APGqIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiICcqVxRRYBERSAiIgCIiAkIUUqAcUUlQpARSoQHIIoCnKgBQUJUIgEU4UKQEREByK4qVCAIiIAiIgCIiAIiIAiIgCIiAIiICVCnKhAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAFyHJcUQElQiIAiKUAwoXJQeagEIiKQEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREARFKABCmUUAhERSAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIinCAhFJUIAiIgCIiAIiIAiIgCkKFKAhERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAFKhEBKhEQBERAEREAREQBERAEREAREQBERAEREAREQBFKICEREAREQBERAFOEHNSoBxREUgIiIAiIgCIiAIiIAiIgCIiAKcqEQElQiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAlFCIAiIgCIiAIiIApJUIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgClQpCAhFKhAEREAREQBERAEREARSmEBCIiAIiIAiIgCIiAIiIAinChAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAFIKhEARF30VJU1s4gpIHzSHqaOQ7T2DvKJXB0Ist0jpWhu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pHOdDeKmnB5NFWxwHxCtFXsd0QHHo9aPi7A+SI/mFovpJT7bvioLnHmSsJ7Rwkv/AI68yrq0/wBht64bJNNROxDtCt7Mj0RMxvH3hytc+yqkA+w1/pp5/wBpI5n5Fa0yUyueWKwstKNv+TKupT/b8zN6zZxVQEhmqdKzY+7cd3+80K1VGj7jCMivssg/Bcoj+ax3KLnlUovSFvH+CrlDl8yvrLRWUwzI6mcOf2dQx/0Kt6lQsJNPRFHbgFIKhSqkEplcVKiwBKhEUgIiIAiIgJAUIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgC57ztzc3ju88Z4LiFKAIijKgBMqESwJKhEUgIilAMooUoCEREAUhQiA5KCp6lxUIBERSCc8EUIgCIiAkKVxU5UWBKgplQiAUhQpCkAqERAEREAREQBEUoAFKBFABXFSVCkBTwUIgJ4IoRAclBRQosCURQpAREQBFOEwgIREQEoVCIAiKUBCIiAIiICQpK4qcqAQuWVxRSCSoREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERASmVCICSoREAREQBERAFy6lxRAFOVCIAiIgCIiAlQiIApwoXJAcUREAREQBERAEREAREQBERAEREARSoQBcguK5KGAiIoBChSoVgERSEBCLkowouCERFIJUIiAIiICQpXFSgChEQBERAFKhEBKhEQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQEhMqEQE5RQiAIilAQpUIgJymVCIAiIgCIiAIiIApChTlACoREAREQBERAEREAREQBFIU4UXBxRSoUgIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgCIiAIiIAiIgJ6lCIoQCIikBERAEREAREQBERAEREAREQBERAEREAREQBERAEREAREQH/2Q==')
    return Response(data, mimetype='image/png')

def verify_hcaptcha(token):
    secret = os.environ.get('HCAPTCHA_SECRET', '')
    if not secret:
        return True  # если ключ не задан — пропускаем
    try:
        data = urllib.parse.urlencode({'secret': secret, 'response': token}).encode()
        req = urllib.request.Request('https://hcaptcha.com/siteverify', data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
            return result.get('success', False)
    except Exception as e:
        print(f"[HCAPTCHA ERROR] {e}")
        return False

@app.route('/api/register', methods=['POST'])
def register():
    d = request.get_json() or {}
    email    = (d.get('email') or '').strip().lower()
    nickname = (d.get('nickname') or '').strip()
    password =  d.get('password') or ''
    hcaptcha_token = d.get('hcaptcha_token') or ''
    if not verify_hcaptcha(hcaptcha_token):
        return jsonify({'ok':False,'error':'Капча не пройдена. Попробуй снова.'}),400
    if not email_ok(email): return jsonify({'ok':False,'error':'Неверный формат email.'}),400
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT email FROM users WHERE email=%s', (email,))
    existing = cur.fetchone()
    cur.execute('SELECT email FROM users WHERE LOWER(nickname)=%s', (nickname.lower(),))
    nick_taken = cur.fetchone()
    cur.close(); conn.close()
    if existing:   return jsonify({'ok':False,'error':'Email уже зарегистрирован.'}),409
    ok,err = nick_ok(nickname)
    if not ok: return jsonify({'ok':False,'error':err}),400
    if nick_taken: return jsonify({'ok':False,'error':'Никнейм уже занят.'}),409
    ok,err = pass_ok(password)
    if not ok: return jsonify({'ok':False,'error':err}),400
    pending_codes[email] = {'expires_at':time.time()+600,'nickname':nickname,'password_hash':hashpw(password)}
    return jsonify({'ok':True})

@app.route('/api/verify', methods=['POST'])
def verify():
    d = request.get_json() or {}
    email = (d.get('email') or '').strip().lower()
    code  = (d.get('code') or '').strip()
    p = pending_codes.get(email)
    if not p: return jsonify({'ok':False,'error':'Нет активного кода.'}),400
    if time.time() > p['expires_at']:
        del pending_codes[email]; return jsonify({'ok':False,'error':'Код истёк.'}),400
    if p.get('code') and p['code'] != code: return jsonify({'ok':False,'error':'Неверный код.'}),400
    conn = get_db(); cur = conn.cursor()
    cur.execute('INSERT INTO users (email,nickname,password_hash,avatar_color,avatar_emoji,created_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (email) DO UPDATE SET nickname=EXCLUDED.nickname',
        (email,p['nickname'],p['password_hash'],random.choice(AVATAR_COLORS),random.choice(AVATAR_EMOJIS),time.time()))
    conn.commit(); cur.close(); conn.close()
    del pending_codes[email]
    token = make_token(p['nickname'])
    return jsonify({'ok':True,'nickname':p['nickname'],'token':token})

@app.route('/api/resend', methods=['POST'])
def resend():
    d = request.get_json() or {}
    email = (d.get('email') or '').strip().lower()
    p = pending_codes.get(email)
    if not p: return jsonify({'ok':False,'error':'Нет активной регистрации.'}),400
    code = mkcode()
    pending_codes[email].update({'code':code,'expires_at':time.time()+600})
    if not send_email(email, p['nickname'], code):
        return jsonify({'ok':False,'error':'Не удалось отправить письмо.'}),500
    return jsonify({'ok':True})

@app.route('/api/login', methods=['POST'])
def login():
    d = request.get_json() or {}
    email    = (d.get('email') or '').strip().lower()
    password =  d.get('password') or ''
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE email=%s', (email,))
    u = cur.fetchone()
    cur.close(); conn.close()
    if not u: return jsonify({'ok':False,'error':'Пользователь не найден.'}),404
    if u['password_hash'] != hashpw(password): return jsonify({'ok':False,'error':'Неверный пароль.'}),401
    token = make_token(u['nickname'])
    return jsonify({'ok':True,'nickname':u['nickname'],'token':token})

@app.route('/api/logout', methods=['POST'])
@require_auth
def logout():
    token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    conn = get_db(); cur = conn.cursor()
    cur.execute('DELETE FROM tokens WHERE token=%s', (token,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/users', methods=['GET'])
@require_auth
def get_users():
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT nickname, avatar_color, avatar_emoji FROM users')
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify({'ok':True,'users':[{'nickname':r['nickname'],'online':r['nickname'] in online,'avatar_color':r['avatar_color'],'avatar_emoji':r['avatar_emoji']} for r in rows]})

@app.route('/api/profile', methods=['GET'])
@require_auth
def get_profile_route():
    nick = (request.args.get('nick') or '').strip()
    if not nick: return jsonify({'ok':False}),400
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE LOWER(nickname)=%s', (nick.lower(),))
    u = cur.fetchone()
    cur.close(); conn.close()
    if not u: return jsonify({'ok':False,'error':'Не найден.'}),404
    return jsonify({'ok':True,'profile':{'nickname':u['nickname'],'avatar_color':u['avatar_color'],'avatar_emoji':u['avatar_emoji'],'online':u['nickname'] in online,'created_at':u['created_at']}})

@app.route('/api/profile/update', methods=['POST'])
@require_auth
def update_profile():
    d = request.get_json() or {}
    nick = request.nickname
    conn = get_db(); cur = conn.cursor()
    if 'avatar_color' in d and d['avatar_color'] in AVATAR_COLORS:
        cur.execute('UPDATE users SET avatar_color=%s WHERE nickname=%s', (d['avatar_color'], nick))
    if 'avatar_emoji' in d and d['avatar_emoji'] in AVATAR_EMOJIS:
        cur.execute('UPDATE users SET avatar_emoji=%s WHERE nickname=%s', (d['avatar_emoji'], nick))
    conn.commit(); cur.close(); conn.close()
    socketio.emit('profile_updated', {'nickname': nick, 'profile': get_profile(nick)})
    return jsonify({'ok':True})

@app.route('/api/messages', methods=['GET'])
@require_auth
def get_messages():
    try:
        other = (request.args.get('with') or '').strip()
        me = request.nickname
        if not other: return jsonify({'ok':False}),400
        conn = get_db(); cur = conn.cursor()
        cur.execute('SELECT * FROM messages WHERE (sender=%s AND receiver=%s) OR (sender=%s AND receiver=%s) ORDER BY time_ms ASC',(me,other,other,me))
        rows = cur.fetchall()
        msg_ids = [r['id'] for r in rows]
        reactions_map = {}
        if msg_ids:
            placeholders = ','.join(['%s']*len(msg_ids))
            cur.execute('SELECT msg_id, emoji, nickname FROM reactions WHERE msg_id IN ('+placeholders+')', msg_ids)
            for rx in cur.fetchall():
                reactions_map.setdefault(rx['msg_id'], {}).setdefault(rx['emoji'], []).append(rx['nickname'])
        cur.close(); conn.close()
        msgs = []
        for r in rows:
            deleted_for = r['deleted_for'].split(',') if r['deleted_for'] else []
            if me in deleted_for or '__all__' in deleted_for: continue
            msgs.append({
                'id': r['id'],
                'from': r['sender'],
                'to': r['receiver'],
                'text': r['text'],
                'type': r['msg_type'] or 'text',
                'caption': r['caption'] or '',
                'time': int(r['time_ms']),
                'edited': bool(r.get('edited', False)),
                'reactions': reactions_map.get(r['id'], {})
            })
        return jsonify({'ok':True,'messages':msgs})
    except Exception as e:
        import traceback
        print('[GET_MESSAGES ERROR]', traceback.format_exc())
        return jsonify({'ok':False,'error':str(e)}),500

@app.route('/api/friends', methods=['GET'])
@require_auth
def get_friends():
    nick = request.nickname
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM friends WHERE user1=%s OR user2=%s', (nick, nick))
    rows = cur.fetchall()
    cur.close(); conn.close()
    friends, sent, received, blocked = [], [], [], []
    for r in rows:
        other = r['user2'] if r['user1'] == nick else r['user1']
        p = get_profile(other)
        if r['status'] == 'friends': friends.append({'nickname':other,'online':other in online,**p})
        elif r['status'] == 'pending' and r['user1'] == nick: sent.append(other)
        elif r['status'] == 'pending' and r['user2'] == nick: received.append(other)
        elif r['status'] == f'blocked_by_{nick}': blocked.append(other)
    return jsonify({'ok':True,'friends':friends,'sent':sent,'received':received,'blocked':blocked})

@app.route('/api/friends/send', methods=['POST'])
@require_auth
def send_friend_request():
    d = request.get_json() or {}
    me = request.nickname
    target = (d.get('to') or '').strip()
    if not target: return jsonify({'ok':False,'error':'Нет данных.'}),400
    if me.lower()==target.lower(): return jsonify({'ok':False,'error':'Нельзя добавить себя.'}),400
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT nickname FROM users WHERE LOWER(nickname)=%s', (target.lower(),))
    tu = cur.fetchone()
    if not tu: cur.close(); conn.close(); return jsonify({'ok':False,'error':'Пользователь не найден.'}),404
    target = tu['nickname']
    cur.execute('SELECT * FROM friends WHERE (user1=%s AND user2=%s) OR (user1=%s AND user2=%s)',(me,target,target,me))
    ex = cur.fetchone()
    if ex:
        if ex['status']=='friends': cur.close(); conn.close(); return jsonify({'ok':False,'error':'Уже в друзьях.'}),400
        if ex['status']=='pending' and ex['user1']==me: cur.close(); conn.close(); return jsonify({'ok':False,'error':'Заявка уже отправлена.'}),400
        if ex['status']=='pending' and ex['user2']==me:
            cur.execute('UPDATE friends SET status=%s WHERE (user1=%s AND user2=%s) OR (user1=%s AND user2=%s)',('friends',me,target,target,me))
            conn.commit(); cur.close(); conn.close()
            for n in [me,target]:
                sid=online.get(n)
                if sid: socketio.emit('friends_update',{},to=sid)
            return jsonify({'ok':True,'message':f'Вы теперь друзья с {target}!'})
    cur.execute('INSERT INTO friends (user1,user2,status) VALUES (%s,%s,%s) ON CONFLICT (user1,user2) DO UPDATE SET status=%s',(me,target,'pending','pending'))
    conn.commit(); cur.close(); conn.close()
    rsid=online.get(target)
    if rsid: socketio.emit('friend_request',{'from':me},to=rsid)
    return jsonify({'ok':True,'message':f'Заявка отправлена {target}!'})

@app.route('/api/friends/accept', methods=['POST'])
@require_auth
def accept_friend():
    d=request.get_json() or {}
    me=request.nickname; sender=(d.get('from') or '').strip()
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT * FROM friends WHERE user1=%s AND user2=%s AND status=%s',(sender,me,'pending'))
    row=cur.fetchone()
    if not row: cur.close(); conn.close(); return jsonify({'ok':False,'error':'Заявки нет.'}),400
    cur.execute('UPDATE friends SET status=%s WHERE user1=%s AND user2=%s',('friends',sender,me))
    conn.commit(); cur.close(); conn.close()
    for n in [me,sender]:
        sid=online.get(n)
        if sid: socketio.emit('friends_update',{},to=sid)
    return jsonify({'ok':True})

@app.route('/api/friends/decline', methods=['POST'])
@require_auth
def decline_friend():
    d=request.get_json() or {}
    me=request.nickname; sender=(d.get('from') or '').strip()
    conn=get_db(); cur=conn.cursor()
    cur.execute('DELETE FROM friends WHERE user1=%s AND user2=%s AND status=%s',(sender,me,'pending'))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/friends/block', methods=['POST'])
@require_auth
def block_user():
    d=request.get_json() or {}
    me=request.nickname; target=(d.get('target') or '').strip()
    conn=get_db(); cur=conn.cursor()
    cur.execute('DELETE FROM friends WHERE (user1=%s AND user2=%s) OR (user1=%s AND user2=%s)',(me,target,target,me))
    cur.execute('INSERT INTO friends (user1,user2,status) VALUES (%s,%s,%s) ON CONFLICT (user1,user2) DO UPDATE SET status=%s',(me,target,f'blocked_by_{me}',f'blocked_by_{me}'))
    conn.commit(); cur.close(); conn.close()
    sid=online.get(me)
    if sid: socketio.emit('friends_update',{},to=sid)
    return jsonify({'ok':True})

@app.route('/api/friends/unblock', methods=['POST'])
@require_auth
def unblock_user():
    d=request.get_json() or {}
    me=request.nickname; target=(d.get('target') or '').strip()
    conn=get_db(); cur=conn.cursor()
    cur.execute('DELETE FROM friends WHERE user1=%s AND user2=%s AND status=%s',(me,target,f'blocked_by_{me}'))
    conn.commit(); cur.close(); conn.close()
    for n in [me, target]:
        sid=online.get(n)
        if sid: socketio.emit('friends_update',{},to=sid)
    return jsonify({'ok':True})

@app.route('/api/messages/edit', methods=['POST'])
@require_auth
def edit_message():
    d=request.get_json() or {}
    msg_id=(d.get('id') or '').strip()
    new_text=(d.get('text') or '').strip()
    me=request.nickname
    if not msg_id or not new_text: return jsonify({'ok':False,'error':'Нет данных.'}),400
    if len(new_text)>2000: return jsonify({'ok':False,'error':'Слишком длинно.'}),400
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT * FROM messages WHERE id=%s',(msg_id,))
    row=cur.fetchone()
    if not row: cur.close(); conn.close(); return jsonify({'ok':False,'error':'Не найдено.'}),404
    if row['sender']!=me: cur.close(); conn.close(); return jsonify({'ok':False,'error':'Нельзя редактировать чужое.'}),403
    if row['msg_type']!='text': cur.close(); conn.close(); return jsonify({'ok':False,'error':'Только текст.'}),400
    cur.execute('UPDATE messages SET text=%s, edited=TRUE WHERE id=%s',(new_text,msg_id))
    conn.commit(); cur.close(); conn.close()
    for n in [row['sender'],row['receiver']]:
        sid=online.get(n)
        if sid: socketio.emit('message_edited',{'id':msg_id,'text':new_text},to=sid)
    return jsonify({'ok':True})

@app.route('/api/messages/react', methods=['POST'])
@require_auth
def react_message():
    d=request.get_json() or {}
    msg_id=(d.get('id') or '').strip()
    emoji=(d.get('emoji') or '').strip()
    me=request.nickname
    if not msg_id or not emoji: return jsonify({'ok':False,'error':'Нет данных.'}),400
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT * FROM messages WHERE id=%s',(msg_id,))
    row=cur.fetchone()
    if not row: cur.close(); conn.close(); return jsonify({'ok':False,'error':'Не найдено.'}),404
    # Если уже поставил эту реакцию — убираем
    cur.execute('SELECT * FROM reactions WHERE msg_id=%s AND nickname=%s',(msg_id,me))
    existing=cur.fetchone()
    if existing and existing['emoji']==emoji:
        cur.execute('DELETE FROM reactions WHERE msg_id=%s AND nickname=%s',(msg_id,me))
    else:
        cur.execute('INSERT INTO reactions (msg_id,nickname,emoji) VALUES (%s,%s,%s) ON CONFLICT (msg_id,nickname) DO UPDATE SET emoji=%s',(msg_id,me,emoji,emoji))
    conn.commit()
    # Получаем обновлённые реакции
    cur.execute('SELECT emoji, nickname FROM reactions WHERE msg_id=%s',(msg_id,))
    rxs=cur.fetchall()
    cur.close(); conn.close()
    reactions={}
    for rx in rxs:
        reactions[rx['emoji']]=reactions.get(rx['emoji'],[])
        reactions[rx['emoji']].append(rx['nickname'])
    for n in [row['sender'],row['receiver']]:
        sid=online.get(n)
        if sid: socketio.emit('reaction_updated',{'id':msg_id,'reactions':reactions},to=sid)
    return jsonify({'ok':True,'reactions':reactions})

@app.route('/api/messages/delete', methods=['POST'])
@require_auth
def delete_message():
    d=request.get_json() or {}
    msg_id=(d.get('id') or '').strip(); me=request.nickname; mode=d.get('mode','me')
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT * FROM messages WHERE id=%s',(msg_id,))
    row=cur.fetchone()
    if not row: cur.close(); conn.close(); return jsonify({'ok':False,'error':'Не найдено.'}),404
    if mode=='all':
        if row['sender']!=me: cur.close(); conn.close(); return jsonify({'ok':False,'error':'Нельзя удалить чужое.'}),403
        cur.execute('UPDATE messages SET deleted_for=%s,text=%s,msg_type=%s WHERE id=%s',('__all__','Сообщение удалено','text',msg_id))
        conn.commit(); cur.close(); conn.close()
        for n in [row['sender'],row['receiver']]:
            sid=online.get(n)
            if sid: socketio.emit('message_deleted',{'id':msg_id,'text':'Сообщение удалено'},to=sid)
    else:
        deleted=row['deleted_for'].split(',') if row['deleted_for'] else []
        if me not in deleted: deleted.append(me)
        cur.execute('UPDATE messages SET deleted_for=%s WHERE id=%s',(','.join(deleted),msg_id))
        conn.commit(); cur.close(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/upload', methods=['POST'])
@require_auth
def upload_file():
    try:
        if not os.environ.get('CLOUDINARY_CLOUD_NAME'):
            return jsonify({'ok': False, 'error': 'Загрузка файлов не настроена. Задайте CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY и CLOUDINARY_API_KEY_SECRET.'}), 503
        if 'file' not in request.files:
            return jsonify({'ok': False, 'error': 'Файл не найден.'}), 400
        f = request.files['file']
        if not f.filename:
            return jsonify({'ok': False, 'error': 'Пустой файл.'}), 400
        mime = f.mimetype or ''
        if mime.startswith('image/'):
            rtype = 'image'
        elif mime.startswith('video/'):
            rtype = 'video'
        elif mime.startswith('audio/'):
            rtype = 'audio'
        else:
            rtype = 'raw'
        f.seek(0, 2)
        size = f.tell()
        f.seek(0)
        if size > 50 * 1024 * 1024:
            return jsonify({'ok': False, 'error': 'Файл слишком большой (макс 50MB).'}), 400
        result = cloudinary.uploader.upload(
            f,
            resource_type=rtype,
            folder='zynx',
            use_filename=True,
            unique_filename=True
        )
        return jsonify({
            'ok': True,
            'url': result['secure_url'],
            'type': rtype,
            'name': f.filename,
            'size': size
        })
    except cloudinary.exceptions.Error as e:
        print('[CLOUDINARY ERROR]', e)
        return jsonify({'ok': False, 'error': f'Ошибка Cloudinary: {e}'}), 500
    except Exception as e:
        import traceback
        print('[UPLOAD ERROR]', traceback.format_exc())
        return jsonify({'ok': False, 'error': str(e)}), 500

@socketio.on('join')
def on_join(data):
    nickname=(data.get('nickname') or '').strip(); token=(data.get('token') or '').strip()
    if not nickname or not token: return
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT nickname FROM tokens WHERE token=%s AND nickname=%s',(token,nickname))
    row=cur.fetchone()
    cur.close(); conn.close()
    if not row: return
    online[nickname]=request.sid
    emit('user_status',{'nickname':nickname,'online':True},broadcast=True)
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT nickname,avatar_color,avatar_emoji FROM users')
    rows=cur.fetchall()
    cur.close(); conn.close()
    snap={r['nickname']:{'avatar_color':r['avatar_color'],'avatar_emoji':r['avatar_emoji']} for r in rows if r['nickname'] in online}
    emit('profiles_snapshot',snap)

@socketio.on('disconnect')
def on_disconnect():
    for n,sid in list(online.items()):
        if sid==request.sid:
            del online[n]
            emit('user_status',{'nickname':n,'online':False},broadcast=True)
            break

@socketio.on('private_message')
def on_private_message(data):
    token=(data.get('token') or '').strip(); sender=(data.get('from') or '').strip()
    receiver=(data.get('to') or '').strip(); text=(data.get('text') or '').strip()
    msg_type=data.get('type','text')
    caption=(data.get('caption') or '').strip()
    if not token or not sender or not receiver or not text: return
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT nickname FROM tokens WHERE token=%s AND nickname=%s',(token,sender))
    tok=cur.fetchone()
    if not tok: cur.close(); conn.close(); return
    if msg_type=='text' and len(text)>2000: cur.close(); conn.close(); return
    if caption and len(caption)>500: caption=caption[:500]
    cur.execute('SELECT * FROM friends WHERE (user1=%s AND user2=%s AND status=%s) OR (user1=%s AND user2=%s AND status=%s)',
        (sender,receiver,f'blocked_by_{sender}',receiver,sender,f'blocked_by_{receiver}'))
    blocked=cur.fetchone()
    if blocked: cur.close(); conn.close(); return
    msg_id=str(uuid.uuid4())[:8]; ts=int(time.time()*1000)
    cur.execute('INSERT INTO messages (id,sender,receiver,text,msg_type,caption,time_ms,deleted_for) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
        (msg_id,sender,receiver,text,msg_type,caption,ts,''))
    conn.commit(); cur.close(); conn.close()
    out={'id':msg_id,'from':sender,'to':receiver,'text':text,'type':msg_type,'time':ts,'caption':caption}
    emit('new_message',out,to=request.sid)
    rsid=online.get(receiver)
    if rsid and rsid!=request.sid: emit('new_message',out,to=rsid)

@socketio.on('typing')
def on_typing(data):
    rsid=online.get((data.get('to') or '').strip())
    if rsid: emit('typing',{'from':data.get('from','')},to=rsid)

@socketio.on('stop_typing')
def on_stop_typing(data):
    rsid=online.get((data.get('to') or '').strip())
    if rsid: emit('stop_typing',{'from':data.get('from','')},to=rsid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
