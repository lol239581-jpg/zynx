from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
import random, string, re, os, time, hashlib, uuid, urllib.request, urllib.error, urllib.parse
import json as _json
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = "zynx-secret-key-2026-xK9mPqL3vNcR7"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

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
                'edited': bool(r['edited']),
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
