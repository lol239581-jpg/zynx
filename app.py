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
    data = _b64.b64decode('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')
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
