import os, sqlite3, zipfile, subprocess, signal, shutil, psutil, time, datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory, send_file
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit

# Global process tracker
running_procs = {}
start_times = {}

# Initialize SocketIO
socketio = SocketIO()

def get_db():
    db_path = os.path.join(os.getcwd(), 'storage/nehost.db')
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    if not os.path.exists('storage'): os.makedirs('storage')
    db = get_db()
    # User Table
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        fname TEXT, lname TEXT, username TEXT, email TEXT, password TEXT, pfp TEXT DEFAULT 'default.png',
        role TEXT DEFAULT 'free', 
        status TEXT DEFAULT 'active',
        server_limit INTEGER DEFAULT 1,
        notifications TEXT DEFAULT ''
    )''')
    # Server Table
    db.execute('''CREATE TABLE IF NOT EXISTS servers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        user_id INTEGER, name TEXT, folder TEXT, 
        status TEXT, startup TEXT, pid INTEGER,
        server_status TEXT DEFAULT 'active'
    )''')
    # Support Ticket Table
    db.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, subject TEXT, message TEXT, status TEXT DEFAULT 'open', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # Admin Settings Table
    db.execute('''CREATE TABLE IF NOT EXISTS admin_settings (
        id INTEGER PRIMARY KEY, 
        username TEXT, password TEXT,
        popup_title TEXT, popup_msg TEXT, popup_img TEXT, show_popup INTEGER DEFAULT 0
    )''')
    
    # Default Admin credentials
    # This also updates an existing database, so the old
    # neverexits / 1100 credentials are replaced automatically.
    admin_row = db.execute('SELECT id FROM admin_settings WHERE id=1').fetchone()
    if admin_row:
        db.execute(
            'UPDATE admin_settings SET username=?, password=? WHERE id=1',
            ('kawsar9340', '50528090')
        )
    else:
        db.execute(
            'INSERT INTO admin_settings (id, username, password) VALUES (1, ?, ?)',
            ('kawsar9340', '50528090')
        )
    
    db.commit()
    db.close()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'nehost_ultra_pro_max_99'
    app.config['BASE_STORAGE'] = os.path.join(os.getcwd(), 'storage/instances')
    app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'static/uploads')
    
    if not os.path.exists(app.config['BASE_STORAGE']):
        os.makedirs(app.config['BASE_STORAGE'])
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
        
    init_db()
    socketio.init_app(app)

    def get_precise_uptime(start_timestamp):
        if not start_timestamp: return "Offline"
        diff = int(time.time() - start_timestamp)
        months, rem = divmod(diff, 2592000)
        days, rem = divmod(rem, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        
        parts = []
        if months > 0: parts.append(f"{months}mo")
        if days > 0: parts.append(f"{days}d")
        if hours > 0: parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)
    
    @app.route('/')
    def home():
      return render_template('index.html')

    # --- UPDATED SIGNUP ROUTE ---
    @app.route('/signup', methods=['GET', 'POST'])
    def signup():
        if request.method == 'POST':
            fname = request.form.get('fname')
            lname = request.form.get('lname')
            username = request.form.get('username')
            email = request.form.get('email')
            pwd = request.form.get('password')
            cpwd = request.form.get('confirm_password')
            pfp = request.files.get('pfp')

            if pwd != cpwd:
                return jsonify({'status': 'error', 'msg': 'Passwords do not match!'}), 400

            db = get_db()
            # Check if user already exists
            existing_user = db.execute('SELECT id FROM users WHERE email=? OR username=?', (email, username)).fetchone()
            if existing_user:
                db.close()
                return jsonify({'status': 'error', 'msg': 'Email or Username already taken!'}), 400

            pfp_name = 'default.png'
            if pfp:
                pfp_name = secure_filename(pfp.filename)
                pfp.save(os.path.join(app.config['UPLOAD_FOLDER'], pfp_name))

            # Logic added: explicitly setting server_limit to 1 for new signups
            db.execute('''INSERT INTO users 
                (fname, lname, username, email, password, pfp, server_limit, role, status) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (fname, lname, username, email, pwd, pfp_name, 1, 'free', 'active'))
            
            db.commit()
            db.close()
            return jsonify({'status': 'success', 'url': url_for('login')})
        
        return render_template('web/signup.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            email = request.form.get('email')
            pwd = request.form.get('password')
            db = get_db()
            user = db.execute('SELECT * FROM users WHERE (email=? OR username=?) AND password=?', (email, email, pwd)).fetchone()
            db.close()
            
            if user:
                if user['status'] == 'banned':
                    return jsonify({'status': 'banned', 'msg': 'Your account is suspended!'}), 403
                session['user_id'] = user['id']
                return jsonify({'status': 'success', 'url': url_for('dashboard')}), 200
            else:
                return jsonify({'status': 'error', 'msg': 'Invalid credentials!'}), 401
        return render_template('web/login.html')

    @app.route('/dashboard')
    def dashboard():
        if 'user_id' not in session: return redirect(url_for('login'))
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
        db.close()
        if not user or user['status'] != 'active':
            session.clear()
            return redirect(url_for('login'))
        return render_template('web/dashboard.html', user=user)

    @app.route('/profile/update', methods=['POST'])
    def update_profile():
        if 'user_id' not in session: return jsonify({'status': 'error'})
        uid = session['user_id']
        fname = request.form.get('fname')
        lname = request.form.get('lname')
        pwd = request.form.get('password')
        db = get_db()
        if pwd:
            db.execute('UPDATE users SET fname=?, lname=?, password=? WHERE id=?', (fname, lname, pwd, uid))
        else:
            db.execute('UPDATE users SET fname=?, lname=? WHERE id=?', (fname, lname, uid))
        db.commit()
        db.close()
        return jsonify({'status': 'success'})

    @app.route('/ticket/create', methods=['POST'])
    def create_ticket():
        if 'user_id' not in session: return jsonify({'status': 'error'})
        d = request.json
        db = get_db()
        db.execute('INSERT INTO tickets (user_id, subject, message) VALUES (?,?,?)', (session['user_id'], d['subject'], d['message']))
        db.commit()
        db.close()
        return jsonify({'status': 'success'})

    @app.route('/api/announcement')
    def get_announcement():
        db = get_db()
        conf = db.execute('SELECT popup_title, popup_msg, popup_img, show_popup FROM admin_settings WHERE id=1').fetchone()
        db.close()
        return jsonify(dict(conf))

    @app.route('/admin-login', methods=['GET', 'POST'])
    def admin_login():
        if request.method == 'POST':
            user, pwd = request.form.get('username'), request.form.get('password')
            db = get_db()
            admin = db.execute('SELECT * FROM admin_settings WHERE username=? AND password=?', (user, pwd)).fetchone()
            db.close()
            if admin:
                session['admin_logged'] = True
                return redirect(url_for('admin_panel'))
        return render_template('web/admin_login.html')

    @app.route('/admin/panel')
    def admin_panel():
        if not session.get('admin_logged'): return redirect(url_for('admin_login'))
        return render_template('web/admin_panel.html')

    @app.route('/admin/stats')
    def admin_stats():
        if not session.get('admin_logged'): return jsonify({})
        db = get_db()
        users = db.execute('SELECT * FROM users').fetchall()
        user_list = []
        total_cpu = psutil.cpu_percent()
        total_ram = psutil.virtual_memory().percent
        for u in users:
            srvs = db.execute('SELECT * FROM servers WHERE user_id=?', (u['id'],)).fetchall()
            active_srvs = 0
            for s in srvs:
                is_on = False
                if s['pid'] and psutil.pid_exists(s['pid']):
                    try:
                        proc = psutil.Process(s['pid'])
                        if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                            is_on = True
                    except: pass
                elif s['folder'] in running_procs and running_procs[s['folder']].poll() is None:
                    is_on = True
                if is_on: active_srvs += 1
            user_list.append({
                'id': u['id'], 'fname': u['fname'], 'email': u['email'], 
                'srv_count': len(srvs), 'active_srvs': active_srvs,
                'status': u['status'], 'role': u['role'], 'server_limit': u['server_limit']
            })
        db.close()
        return jsonify({'users': user_list, 'sys_cpu': f"{total_cpu}%", 'sys_ram': f"{total_ram}%"})

    @app.route('/admin/user/update', methods=['POST'])
    def update_user():
        if not session.get('admin_logged'): return jsonify({'status':'error'})
        d = request.json
        db = get_db()
        db.execute('UPDATE users SET role=?, status=?, server_limit=? WHERE id=?', (d['role'], d['status'], d['limit'], d['user_id']))
        db.commit()
        db.close()
        return jsonify({'status': 'success'})

    @app.route('/admin/set-popup', methods=['POST'])
    def set_popup():
        if not session.get('admin_logged'): return jsonify({'status':'error'})
        title, msg, show = request.form.get('title'), request.form.get('msg'), request.form.get('show')
        img = request.files.get('image')
        db = get_db()
        old_data = db.execute('SELECT popup_img FROM admin_settings WHERE id=1').fetchone()
        img_name = old_data['popup_img'] if old_data else None
        if img:
            img_name = secure_filename(img.filename)
            img.save(os.path.join(app.config['UPLOAD_FOLDER'], img_name))
        db.execute('UPDATE admin_settings SET popup_title=?, popup_msg=?, popup_img=?, show_popup=? WHERE id=1', (title, msg, img_name, 1 if show == 'true' else 0))
        db.commit()
        db.close()
        return jsonify({'status': 'success'})

    @app.route('/admin/send-warning', methods=['POST'])
    def send_warning():
        if not session.get('admin_logged'): return jsonify({'status': 'error'})
        d = request.json
        db = get_db()
        db.execute('UPDATE users SET notifications=? WHERE id=?', (d['message'], d['user_id']))
        db.commit()
        db.close()
        return jsonify({'status': 'success'})

    @app.route('/admin/login-as/<int:uid>')
    def login_as(uid):
        if not session.get('admin_logged'): return redirect(url_for('admin_login'))
        session['user_id'] = uid
        return redirect(url_for('dashboard'))

    @app.route('/admin/manage-user/<int:uid>')
    def admin_manage_user_servers(uid):
        if not session.get('admin_logged'): return redirect(url_for('admin_login'))
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        rows = db.execute('SELECT * FROM servers WHERE user_id=?', (uid,)).fetchall()
        db.close()
        servers = []
        for r in rows:
            f = r['folder']
            online = (f in running_procs and running_procs[f].poll() is None) or (r['pid'] and psutil.pid_exists(r['pid']))
            servers.append({'id': r['id'], 'name': r['name'], 'folder': f, 'online': online, 'status': r['server_status']})
        return render_template('web/admin_manage_user.html', user=user, servers=servers)

    @app.route('/admin/suspend-server/<int:sid>', methods=['POST'])
    def admin_suspend_server(sid):
        if not session.get('admin_logged'): return jsonify({'status': 'error'})
        status = request.json.get('status')
        db = get_db()
        db.execute('UPDATE servers SET server_status=? WHERE id=?', (status, sid))
        db.commit()
        db.close()
        return jsonify({'status': 'success'})

    @app.route('/admin/delete-server/<int:sid>', methods=['POST'])
    def admin_delete_server(sid):
        if not session.get('admin_logged'): return jsonify({'status': 'error'})
        db = get_db()
        srv = db.execute('SELECT folder FROM servers WHERE id=?', (sid,)).fetchone()
        if srv:
            folder = srv['folder']
            if folder in running_procs:
                try: os.killpg(os.getpgid(running_procs[folder].pid), signal.SIGKILL)
                except: pass
                del running_procs[folder]
            db.execute('DELETE FROM servers WHERE id=?', (sid,))
            db.commit()
            path = os.path.join(app.config['BASE_STORAGE'], folder)
            if os.path.exists(path): shutil.rmtree(path)
            db.close()
            return jsonify({'status': 'deleted'})
        db.close()
        return jsonify({'status': 'error', 'msg': 'Server not found'})

    @app.route('/admin/create-user', methods=['POST'])
    def admin_create_user():
        if not session.get('admin_logged'): return jsonify({'status': 'error'})
        d = request.json
        db = get_db()
        limit = d.get('limit', 1)
        db.execute('INSERT INTO users (fname, email, password, server_limit) VALUES (?,?,?,?)', (d['name'], d['email'], d['pass'], limit))
        db.commit()
        db.close()
        return jsonify({'status': 'success'})

    @app.route('/admin/delete-user/<int:uid>', methods=['POST'])
    def delete_user(uid):
        if not session.get('admin_logged'): return jsonify({'status': 'error'})
        db = get_db()
        srvs = db.execute('SELECT folder FROM servers WHERE user_id=?', (uid,)).fetchall()
        for s in srvs:
            path = os.path.join(app.config['BASE_STORAGE'], s['folder'])
            if os.path.exists(path): shutil.rmtree(path)
        db.execute('DELETE FROM servers WHERE user_id=?', (uid,))
        db.execute('DELETE FROM users WHERE id=?', (uid,))
        db.commit()
        db.close()
        return jsonify({'status': 'deleted'})
        
    @app.route('/admin/files/<folder>')
    def admin_browse_files(folder):
        if not session.get('admin_logged'): return redirect(url_for('admin_login'))
        return render_template('web/dashboard.html', user={'fname': 'Admin'}, is_admin_view=True, admin_folder=folder)

    @app.route('/files/list/<folder>')
    def flist(folder):
        sub_path = request.args.get('path', '')
        full_path = os.path.normpath(os.path.join(app.config['BASE_STORAGE'], folder, sub_path))
        if not full_path.startswith(app.config['BASE_STORAGE']): return jsonify([])
        if not os.path.exists(full_path): return jsonify([])
        items = []
        for f in sorted(os.listdir(full_path)):
            if f == 'console.log': continue
            p = os.path.join(full_path, f)
            items.append({'name': f, 'is_dir': os.path.isdir(p), 'is_zip': f.lower().endswith('.zip'), 'rel_path': os.path.join(sub_path, f)})
        return jsonify(items)

    @app.route('/files/content/<folder>/<name>')
    def fcontent(folder, name):
        sub_path = request.args.get('path', '')
        p = os.path.join(app.config['BASE_STORAGE'], folder, sub_path, name)
        try:
            with open(p, 'r', encoding='utf-8', errors='ignore') as f: return jsonify({'content': f.read()})
        except: return jsonify({'content': 'Error reading file'})

    @app.route('/files/save/<folder>/<name>', methods=['POST'])
    def fsave(folder, name):
        sub_path = request.args.get('path', '')
        p = os.path.join(app.config['BASE_STORAGE'], folder, sub_path, name)
        try:
            with open(p, 'w', encoding='utf-8') as f: f.write(request.json.get('content'))
            return jsonify({'status': 'saved'})
        except: return jsonify({'status': 'error'})

    @app.route('/files/delete-bulk/<folder>', methods=['POST'])
    def delete_bulk(folder):
        d = request.json
        sub_path, names = d.get('path', ''), d.get('names', [])
        base = os.path.join(app.config['BASE_STORAGE'], folder, sub_path)
        if not names: names = [f for f in os.listdir(base) if f != 'console.log']
        for name in names:
            p = os.path.join(base, name)
            if name == 'console.log': continue
            try:
                if os.path.isdir(p): shutil.rmtree(p)
                elif os.path.exists(p): os.remove(p)
            except: pass
        return jsonify({"status": "ok"})

    @app.route('/files/create-file/<folder>', methods=['POST'])
    def create_file(folder):
        d = request.json
        p = os.path.join(app.config['BASE_STORAGE'], folder, d.get('path', ''), secure_filename(d.get('name')))
        with open(p, 'w') as f: f.write("")
        return jsonify({'status': 'success'})

    @app.route('/files/create-folder/<folder>', methods=['POST'])
    def create_folder(folder):
        d = request.json
        p = os.path.join(app.config['BASE_STORAGE'], folder, d.get('path', ''), secure_filename(d.get('name')))
        os.makedirs(p, exist_ok=True)
        return jsonify({'status': 'success'})

    @app.route('/files/upload/<folder>', methods=['POST'])
    def upload_file(folder):
        sub_path = request.form.get('path', '')
        file = request.files['file']
        dest = os.path.join(app.config['BASE_STORAGE'], folder, sub_path)
        if not os.path.exists(dest): os.makedirs(dest)
        file.save(os.path.join(dest, secure_filename(file.filename)))
        return jsonify({'status': 'success'})

    @app.route('/files/rename/<folder>', methods=['POST'])
    def rename_file(folder):
        d = request.json
        base = os.path.join(app.config['BASE_STORAGE'], folder, d.get('path', ''))
        os.rename(os.path.join(base, d['old']), os.path.join(base, d['new']))
        return jsonify({'status': 'success'})

    @app.route('/files/download/<folder>/<name>')
    def download_file(folder, name):
        sub_path = request.args.get('path', '')
        p = os.path.normpath(os.path.join(app.config['BASE_STORAGE'], folder, sub_path, name))
        if not p.startswith(app.config['BASE_STORAGE']): return "Access Denied", 403
        return send_file(p, as_attachment=True)

    @app.route('/files/zip-bulk/<folder>', methods=['POST'])
    def zip_bulk(folder):
        d = request.json
        names, sub_path = d.get('names', []), d.get('path', '')
        base = os.path.join(app.config['BASE_STORAGE'], folder, sub_path)
        if not names: names = [f for f in os.listdir(base) if f != 'console.log']
        zip_name = f"archive_{int(time.time())}.zip"
        zip_path = os.path.join(base, zip_name)
        with zipfile.ZipFile(zip_path, 'w') as z:
            for n in names:
                p = os.path.join(base, n)
                if n == zip_name: continue
                if os.path.isdir(p):
                    for root, dirs, files in os.walk(p):
                        for file in files:
                            full_p = os.path.join(root, file)
                            z.write(full_p, os.path.relpath(full_p, base))
                elif os.path.exists(p): z.write(p, n)
        return jsonify({'status': 'success', 'zip': zip_name})

    @app.route('/files/unzip/<folder>', methods=['POST'])
    def unzip_file(folder):
        d = request.json
        zip_name = d.get('name')
        sub_path = d.get('path', '')
        
        # Path safety calculation
        base = os.path.join(app.config['BASE_STORAGE'], folder, sub_path)
        zip_path = os.path.join(base, zip_name)
        
        if os.path.exists(zip_path) and zipfile.is_zipfile(zip_path):
            try:
                with zipfile.ZipFile(zip_path, 'r') as z:
                    # Extracts exactly into the current directory
                    z.extractall(base)
                return jsonify({'status': 'success'})
            except Exception as e:
                return jsonify({'status': 'error', 'msg': str(e)})
        return jsonify({'status': 'error', 'msg': 'Invalid zip file'})

    @app.route('/server/action/<folder>/<act>', methods=['POST'])
    def server_action(folder, act):
        db = get_db()
        srv_data = db.execute('SELECT server_status FROM servers WHERE folder=?', (folder,)).fetchone()
        if srv_data and srv_data['server_status'] == 'suspended':
            db.close()
            return jsonify({'status': 'error', 'msg': 'This server is suspended by Admin.'})

        path = os.path.join(app.config['BASE_STORAGE'], folder)
        log_file_path = os.path.join(path, 'console.log')
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if act == 'install':
            req_path = os.path.join(path, 'requirements.txt')
            if os.path.exists(req_path):
                f_log = open(log_file_path, 'a')
                f_log.write(f"\n[{now}] 📦 Package Installation Started...\n")
                f_log.flush()
                subprocess.Popen(['pip', 'install', '-r', 'requirements.txt'], cwd=path, stdout=f_log, stderr=f_log)
                db.close()
                return jsonify({'status': 'installing'})
            db.close()
            return jsonify({'status': 'error', 'msg': 'requirements.txt missing'})

        if act in ['start', 'restart']:
            row = db.execute('SELECT pid FROM servers WHERE folder=?', (folder,)).fetchone()
            old_pid = row['pid'] if row else None
            if folder in running_procs or (old_pid and psutil.pid_exists(old_pid)):
                try: 
                    t_pid = running_procs[folder].pid if folder in running_procs else old_pid
                    os.killpg(os.getpgid(t_pid), signal.SIGKILL)
                except: pass
            srv = db.execute('SELECT startup FROM servers WHERE folder=?', (folder,)).fetchone()
            startup_file = srv['startup'] if srv and srv['startup'] else 'main.py'
            f_log = open(log_file_path, 'a')
            f_log.write(f"\n[{now}] 🚀 Instance {act.upper()}ED Successfully\n")
            proc = subprocess.Popen(['python3', startup_file], cwd=path, stdout=f_log, stderr=f_log, preexec_fn=os.setsid)
            running_procs[folder], start_times[folder] = proc, time.time()
            db.execute('UPDATE servers SET pid=? WHERE folder=?', (proc.pid, folder))
            db.commit()
            db.close()
            return jsonify({'status': 'started'})
        elif act == 'stop':
            row = db.execute('SELECT pid FROM servers WHERE folder=?', (folder,)).fetchone()
            t_pid = running_procs[folder].pid if folder in running_procs else (row['pid'] if row else None)
            if t_pid:
                try: os.killpg(os.getpgid(t_pid), signal.SIGKILL)
                except: pass
            if folder in running_procs: del running_procs[folder]
            db.execute('UPDATE servers SET pid=NULL WHERE folder=?', (folder,))
            db.commit()
            db.close()
            with open(log_file_path, 'a') as f: f.write(f"\n[{now}] 🛑 Instance STOPPED\n")
            return jsonify({'status': 'stopped'})
        db.close()
        return jsonify({'status': 'ok'})

    @app.route('/server/log/<folder>')
    def server_log(folder):
        path = os.path.join(app.config['BASE_STORAGE'], folder, 'console.log')
        if os.path.exists(path):
            with open(path, 'r') as f: return jsonify({'log': f.read()[-5000:]})
        return jsonify({'log': 'Waiting for logs...'})

    @app.route('/server/set-startup/<folder>', methods=['POST'])
    def set_startup(folder):
        cmd = request.json.get('file')
        db = get_db()
        db.execute('UPDATE servers SET startup=? WHERE folder=?', (cmd, folder))
        db.commit()
        db.close()
        return jsonify({'status': 'success'})

    @app.route('/server/delete/<folder>', methods=['POST'])
    def delete_server(folder):
        db = get_db()
        srv_data = db.execute('SELECT server_status, pid FROM servers WHERE folder=?', (folder,)).fetchone()
        if srv_data and srv_data['server_status'] == 'suspended':
            db.close()
            return jsonify({'status': 'error', 'msg': 'Suspended servers cannot be deleted!'})
        t_pid = running_procs[folder].pid if folder in running_procs else (srv_data['pid'] if srv_data else None)
        if t_pid:
            try: os.killpg(os.getpgid(t_pid), signal.SIGKILL)
            except: pass
        if folder in running_procs: del running_procs[folder]
        db.execute('DELETE FROM servers WHERE folder=?', (folder,))
        db.commit()
        db.close()
        path = os.path.join(app.config['BASE_STORAGE'], folder)
        if os.path.exists(path): shutil.rmtree(path)
        return jsonify({'status': 'deleted'})

    @app.route('/servers')
    def list_servers():
        if 'user_id' not in session: return jsonify({'servers': []})
        db = get_db()
        rows = db.execute('SELECT * FROM servers WHERE user_id=?', (session['user_id'],)).fetchall()
        db.close()
        srvs = []
        for r in rows:
            f, saved_pid = r['folder'], r['pid']
            online = False
            if saved_pid and psutil.pid_exists(saved_pid):
                try:
                    p = psutil.Process(saved_pid)
                    if p.is_running() and p.status() != psutil.STATUS_ZOMBIE: online = True
                except: pass
            elif f in running_procs and running_procs[f].poll() is None: online = True
            uptime = get_precise_uptime(start_times.get(f)) if online and f in start_times else ("Online" if online else "Offline")
            cpu, ram = "0%", "0MB"
            if online:
                try:
                    p_pid = running_procs[f].pid if f in running_procs else saved_pid
                    process = psutil.Process(p_pid)
                    cpu, ram = f"{process.cpu_percent(interval=None)}%", f"{process.memory_info().rss / (1024 * 1024):.1f}MB"
                except: pass
            srvs.append({'name': r['name'], 'folder': f, 'online': online, 'startup': r['startup'], 'uptime': uptime, 'cpu': cpu, 'ram': ram, 'status': r['server_status']})
        return jsonify({'servers': srvs})

    @app.route('/add', methods=['POST'])
    def add_srv():
        if 'user_id' not in session: return jsonify({'status': 'error'})
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
        count = db.execute('SELECT COUNT(*) as count FROM servers WHERE user_id=?', (session['user_id'],)).fetchone()['count']
        if count >= user['server_limit']:
            db.close()
            return jsonify({'status': 'error', 'msg': f"Limit Reached! Max: {user['server_limit']}"})
        name = request.json.get('name')
        folder = secure_filename(name).lower() + "_" + str(int(time.time()))
        db.execute('INSERT INTO servers (user_id, name, folder, status, startup) VALUES (?,?,?,?,?)', (session['user_id'], name, folder, 'Offline', 'main.py'))
        db.commit()
        db.close()
        os.makedirs(os.path.join(app.config['BASE_STORAGE'], folder), exist_ok=True)
        return jsonify({'status': 'success'})

    return app

app = create_app()


# === AUTO_HOST_SMTP_START ===
_HOST_OTP_STORE = {}

def _get_smtp_cfg():
    import os, json
    p = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'smtp_config.json')
    cfg = {
        "smtp_enabled": True,
        "verification_enabled": True,
        "server_notify_enabled": True,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_pass": "",
        "sender_name": "Hosting Panel"
    }
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg

def _set_smtp_cfg(cfg):
    import os, json
    p = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'smtp_config.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)

def _send_mail_now(to_email, subject, html_body, ignore_master_switch=False):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    cfg = _get_smtp_cfg()
    if not ignore_master_switch and not cfg.get("smtp_enabled", True):
        return False, "SMTP Master Switch OFF kora ache!"

    smtp_user = (cfg.get("smtp_user") or "").strip()
    smtp_pass = (cfg.get("smtp_pass") or "").replace(" ", "").strip()
    smtp_host = (cfg.get("smtp_host") or "smtp.gmail.com").strip()
    smtp_port = int(cfg.get("smtp_port") or 587)
    sender_name = cfg.get("sender_name") or "Hosting Panel"

    if not smtp_user or not smtp_pass:
        return False, "Admin Panel theke Sender Gmail o App Password save korun!"

    msg = MIMEMultipart()
    msg["From"] = f"{sender_name} <{smtp_user}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if smtp_port == 465:
            s = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
        else:
            s = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            s.ehlo()
            s.starttls()
        s.login(smtp_user, smtp_pass)
        s.sendmail(smtp_user, [to_email], msg.as_string())
        s.quit()
        return True, "Email sent successfully!"
    except Exception as e:
        return False, f"SMTP Error: {str(e)}"

def _send_mail_bg(to_email, subject, html_body):
    import threading
    if not to_email or "@" not in str(to_email):
        return
    threading.Thread(target=_send_mail_now, args=(to_email, subject, html_body), daemon=True).start()

def _lookup_logged_in_email():
    from flask import session
    import os, json
    for k in ('email', 'user_email', 'gmail'):
        v = session.get(k)
        if v and '@' in str(v):
            return str(v), str(v)
    u = session.get('username') or session.get('user') or session.get('user_id')
    if u and '@' in str(u):
        return str(u), str(u)
    base = os.path.abspath(os.path.dirname(__file__))
    for fn in os.listdir(base):
        if fn.endswith('.json') and fn != 'smtp_config.json':
            try:
                d = json.load(open(os.path.join(base, fn), 'r', encoding='utf-8'))
                if isinstance(d, dict) and u in d and isinstance(d[u], dict):
                    em = d[u].get('email') or d[u].get('gmail')
                    if em:
                        return str(u), str(em)
            except Exception:
                pass
    return str(u or 'User'), None

@app.before_request
def _host_smtp_before_req():
    from flask import request, jsonify, render_template, flash, g
    import random, time

    path = request.path
    method = request.method

    if path == '/api/auth_config' and method == 'GET':
        cfg = _get_smtp_cfg()
        return jsonify({
            "smtp_enabled": bool(cfg.get("smtp_enabled", True)),
            "verification_enabled": bool(cfg.get("verification_enabled", True)),
            "server_notify_enabled": bool(cfg.get("server_notify_enabled", True))
        })

    if path == '/api/smtp_settings':
        if method == 'GET':
            return jsonify(_get_smtp_cfg())
        if method == 'POST':
            data = request.get_json(silent=True) or request.form or {}
            cfg = _get_smtp_cfg()
            cfg["smtp_enabled"] = bool(data.get("smtp_enabled", True))
            cfg["verification_enabled"] = bool(data.get("verification_enabled", True))
            cfg["server_notify_enabled"] = bool(data.get("server_notify_enabled", True))
            cfg["smtp_host"] = (data.get("smtp_host") or "smtp.gmail.com").strip()
            cfg["smtp_port"] = int(data.get("smtp_port") or 587)
            cfg["sender_name"] = (data.get("sender_name") or "Hosting Panel").strip()
            cfg["smtp_user"] = (data.get("smtp_user") or "").strip()
            cfg["smtp_pass"] = (data.get("smtp_pass") or "").strip()
            _set_smtp_cfg(cfg)
            return jsonify({"success": True, "message": "SMTP & Verification Settings Saved!"})

    if path == '/api/test_smtp' and method == 'POST':
        cfg = _get_smtp_cfg()
        ok, msg = _send_mail_now(
            cfg.get("smtp_user"),
            "✅ Test Email - SMTP Working!",
            "<h3>SMTP Connected!</h3><p>Apnar Hosting Panel-er SMTP email kaj korche.</p>",
            ignore_master_switch=True
        )
        return jsonify({"success": ok, "message": msg})

    if path == '/api/send_otp' and method == 'POST':
        data = request.get_json(silent=True) or request.form or {}
        email = (data.get("email") or "").strip().lower()
        username = (data.get("username") or "User").strip()
        if not email or "@" not in email:
            return jsonify({"success": False, "message": "Sothik Gmail address din!"}), 400

        code = str(random.randint(100000, 999999))
        _HOST_OTP_STORE[email] = {"code": code, "expires": time.time() + 600}
        html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:20px;border:1px solid #e5e7eb;border-radius:12px;">
            <h2 style="color:#10b981;">Verification Code</h2>
            <p>Hello <b>{username}</b>, apnar account verification code:</p>
            <div style="font-size:28px;font-weight:bold;letter-spacing:6px;background:#f3f4f6;padding:14px;text-align:center;border-radius:8px;color:#111827;">
                {code}
            </div>
        </div>
        """
        ok, err = _send_mail_now(email, f"🔐 Verification Code: {code}", html)
        if ok:
            return jsonify({"success": True, "message": f"{email}-e verification code pathano hoyeche!"})
        return jsonify({"success": False, "message": err}), 500

    if method == 'POST' and any(k in path.lower() for k in ('/signup', '/register')):
        cfg = _get_smtp_cfg()
        data = request.get_json(silent=True) or request.form or {}
        email = (data.get('email') or data.get('gmail') or data.get('username') or '').strip().lower()
        otp = (data.get('otp') or '').strip()

        if cfg.get("smtp_enabled", True) and cfg.get("verification_enabled", True):
            saved = _HOST_OTP_STORE.get(email)
            if not saved or saved.get("code") != otp:
                msg = "Invalid Gmail Verification Code (OTP)! Sothik code din."
                if request.is_json:
                    return jsonify({"success": False, "error": msg, "message": msg}), 400
                try:
                    flash(msg, "error")
                except Exception:
                    pass
                return render_template('web/signup.html', error=msg, message=msg)
            _HOST_OTP_STORE.pop(email, None)

        g._new_signup_email = email

    g._srv_act = None
    g._srv_target = None
    lp = path.lower()
    if method == 'POST' and any(k in lp for k in ('/upload', '/create', '/new', '/add', '/deploy')) and not any(x in lp for x in ('admin', 'login', 'signup', 'register')):
        g._srv_act = 'created'
        if request.files:
            fl = list(request.files.values())
            if fl and fl[0].filename:
                g._srv_target = fl[0].filename
        if not g._srv_target:
            d = request.get_json(silent=True) or request.form or {}
            g._srv_target = d.get('name') or d.get('server_name') or d.get('filename') or 'New Server'
    elif any(k in lp for k in ('/delete', '/remove', '/destroy', '/del')) and 'admin' not in lp:
        g._srv_act = 'deleted'
        parts = [p for p in path.split('/') if p]
        g._srv_target = parts[-1] if len(parts) > 1 else 'Server'

@app.after_request
def _host_smtp_after_req(response):
    from flask import g
    from datetime import datetime
    try:
        cfg = _get_smtp_cfg()
        if not cfg.get("smtp_enabled", True):
            return response
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        s_email = getattr(g, '_new_signup_email', None)
        if s_email and '@' in s_email and response.status_code in (200, 201, 302):
            _send_mail_bg(
                s_email,
                "🎉 Welcome! Account Created Successfully",
                f"<h3>Welcome!</h3><p>Apnar account ({s_email}) sofolbhabe toiri hoyeche ({now_str}).</p>"
            )

        act = getattr(g, '_srv_act', None)
        tname = getattr(g, '_srv_target', 'Server')
        if act and cfg.get("server_notify_enabled", True) and response.status_code in (200, 201, 302):
            uname, uemail = _lookup_logged_in_email()
            if uemail and '@' in uemail:
                if act == 'created':
                    _send_mail_bg(
                        uemail,
                        f"🚀 Server Created: {tname}",
                        f"<h3>🚀 New Server Created!</h3><p>Hello <b>{uname}</b>, apnar <b>{tname}</b> server/file toiri hoyeche ({now_str}).</p>"
                    )
                else:
                    _send_mail_bg(
                        uemail,
                        f"🗑️ Server Deleted: {tname}",
                        f"<h3>🗑️ Server Deleted!</h3><p>Hello <b>{uname}</b>, apnar <b>{tname}</b> server/file delete kora hoyeche ({now_str}).</p>"
                    )
    except Exception:
        pass
    return response
# === AUTO_HOST_SMTP_END ===


if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)