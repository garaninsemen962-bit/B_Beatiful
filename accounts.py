"""Profile and administrator edits; password values never enter audit data."""
import hashlib,hmac,re,secrets,time
import store as s

def verify(user,password):
    hashed=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(user['salt']),250000).hex()
    return hmac.compare_digest(user['password'],hashed)

def update(actor,target_id,fields,admin_mode=False):
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE')
        fresh=c.execute('SELECT * FROM users WHERE id=?',(actor['id'],)).fetchone()
        if not fresh or fresh['status']!='active':raise PermissionError('Учётная запись недоступна')
        actor=dict(fresh)
        if admin_mode and not actor['admin']:raise PermissionError('Требуются права администратора')
        if not admin_mode and actor['id']!=target_id:raise PermissionError('Можно изменить только свой профиль')
        row=c.execute('SELECT * FROM users WHERE id=?',(target_id,)).fetchone()
        if not row:raise FileNotFoundError('Пользователь не найден')
        target=dict(row)
        if target['status']=='deleted':raise ValueError('Сначала восстановите пользователя')
        if target.get('server_root') and not s.is_root(actor):raise PermissionError('Изменять root может только root или локальный оператор сервера')
        if int(fields['revision'])!=target['revision']:raise ValueError('Профиль уже изменён. Обновите страницу перед сохранением.')
        login=fields.get('login',target['login']).strip();display=fields.get('display',target['display']).strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{2,32}',login):raise ValueError('Логин: 2–32 латинских символа, цифры, _ или -')
        if login.lower()=='root' and not target.get('server_root'):raise ValueError('Логин root зарезервирован для локального оператора')
        if target.get('server_root') and login!='root':raise ValueError('Логин привилегированной учётной записи root фиксирован')
        if not display or len(display)>100:raise ValueError('Имя: 1–100 символов')
        if c.execute('SELECT id FROM users WHERE login=? AND id!=?',(login,target_id)).fetchone():raise ValueError('Такой логин уже занят')
        password=fields.get('new_password','')
        if target.get('server_root') and password and len(password)<12:raise ValueError('Пароль root: минимум 12 символов')
        if password and len(password)<8:raise ValueError('Новый пароль: минимум 8 символов')
        if password!=fields.get('confirm_password',''):raise ValueError('Новый пароль и подтверждение не совпадают')
        if (not admin_mode or actor['id']==target_id) and (password or login!=target['login']) and not verify(target,fields.get('current_password','')):raise ValueError('Текущий пароль указан неверно')
        status=fields.get('status',target['status']) if admin_mode else target['status']
        if admin_mode and fields.get('admin','0') not in ('0','1'):raise ValueError('Неверная роль')
        is_admin=int(fields.get('admin','0')=='1') if admin_mode else target['admin']
        if target.get('server_root') and (not is_admin or status!='active'):raise ValueError('Права и статус root меняются только локальным оператором')
        if status not in ('pending','active','rejected','blocked'):raise ValueError('Неверный статус')
        if target_id==actor['id'] and admin_mode and (status!='active' or not is_admin):raise ValueError('Нельзя заблокировать себя или снять с себя права администратора')
        if target['admin'] and target['status']=='active' and (not is_admin or status!='active'):
            if c.execute("SELECT count(*) FROM users WHERE admin=1 AND status='active'").fetchone()[0]<=1:raise ValueError('Нельзя отключить последнего активного администратора')
        before={k:target[k] for k in ('login','display','status','admin')};after=dict(login=login,display=display,status=status,admin=is_admin)
        revoke=bool(password or any(before[k]!=after[k] for k in ('login','status','admin')))
        salt=target['salt'];hashed=target['password']
        if password:
            salt=secrets.token_hex(16);hashed=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),250000).hex()
        c.execute('UPDATE users SET login=?,display=?,status=?,admin=?,salt=?,password=?,revision=revision+1 WHERE id=?',(login,display,status,is_admin,salt,hashed,target_id))
        if revoke:c.execute('DELETE FROM sessions WHERE user_id=?',(target_id,))
        s.audit(c,actor,None,'Изменение пользователя' if admin_mode else 'Изменение своего профиля',{'user':target_id,'before':before,'after':after,'password_changed':bool(password),'sessions_revoked':revoke})
    return revoke


def delete_user(actor,uid,expected,restore=False):
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE');fresh=c.execute('SELECT * FROM users WHERE id=?',(actor['id'],)).fetchone()
        if not fresh or not fresh['admin'] or fresh['status']!='active':raise PermissionError('Требуются права администратора')
        actor=dict(fresh);row=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not row:raise FileNotFoundError('Пользователь не найден')
        target=dict(row)
        if int(expected)!=target['revision']:raise ValueError('Пользователь изменён. Обновите страницу подтверждения.')
        if target['id']==actor['id']:raise ValueError('Нельзя удалить собственную учётную запись')
        if target.get('server_root'):raise PermissionError('Учётная запись root защищена от удаления через сайт')
        if restore:
            if target['status']!='deleted':raise ValueError('Пользователь не удалён')
            status='blocked';label='Восстановление удалённого пользователя'
        else:
            if target['status']=='deleted':raise ValueError('Пользователь уже удалён')
            if target['admin'] and target['status']=='active' and c.execute("SELECT count(*) FROM users WHERE admin=1 AND status='active'").fetchone()[0]<=1:raise ValueError('Нельзя удалить последнего активного администратора')
            if c.execute("SELECT count(*) FROM executions WHERE actor=? AND state IN ('queued','running')",(uid,)).fetchone()[0] or c.execute("SELECT count(*) FROM analyses WHERE actor=? AND state IN ('queued','running')",(uid,)).fetchone()[0]:raise ValueError('Дождитесь завершения расчётов и Gemini этого пользователя')
            status='deleted';label='Удаление пользователя'
        c.execute('UPDATE users SET status=?,revision=revision+1 WHERE id=?',(status,uid));c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        s.audit(c,actor,None,label,{'user':uid,'login':target['login'],'previous_status':target['status'],'new_status':status,'resources_retained':True})
