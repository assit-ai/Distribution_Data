'use strict';
const express = require('express');
const { Pool } = require('pg');
const bcrypt = require('bcryptjs');
const cookieSession = require('cookie-session');
const path = require('path');

const prod = process.env.NODE_ENV === 'production';
if (!process.env.DATABASE_URL) { console.error('DATABASE_URL set kora nai'); process.exit(1); }
if (prod && !process.env.SESSION_SECRET) { console.error('SESSION_SECRET set kora nai'); process.exit(1); }

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.PGSSL === 'false' ? false : (prod ? { rejectUnauthorized: false } : false)
});

const app = express();
app.set('trust proxy', 1);
app.use(express.json({ limit: '100kb' }));
app.use(cookieSession({
  name: 'dup', keys: [process.env.SESSION_SECRET || 'dev-only-secret'],
  maxAge: 12 * 60 * 60 * 1000, httpOnly: true, sameSite: 'lax', secure: prod
}));
// Mutating request shudhu JSON hole nibe (basic CSRF protection, sameSite cookie er sathe)
app.use((req, res, next) => {
  if (['POST', 'PUT', 'DELETE'].includes(req.method) && !req.is('application/json') && req.path.startsWith('/api/') && req.method !== 'DELETE')
    return res.status(415).json({ error: 'JSON dorkar' });
  next();
});

const CATS = ['Frozen', 'Chicken', 'Egg', 'Dairy', 'Others'];
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const wrap = fn => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);
const lc = s => String(s || '').trim().toLowerCase();
const cleanDepots = a => [...new Set((Array.isArray(a) ? a : String(a || '').split(','))
  .map(x => String(x).trim()).filter(Boolean).map(x => x.slice(0, 60)))];
const normMobile = s => { const m = String(s || '').replace(/[\s-]/g, ''); return m === '' ? '' : (/^\+?[0-9]{7,15}$/.test(m) ? m : null); };
const last10 = m => String(m || '').replace(/\D/g, '').slice(-10);
const nonNeg = v => { const n = Number(v); return Number.isFinite(n) && n >= 0 && n < 1e12 ? n : null; };

// ---------- DB setup ----------
async function init() {
  await pool.query(`
    create table if not exists users(
      id serial primary key, username text unique not null, password_hash text not null,
      name text not null default '', role text not null default 'depot' check (role in ('admin','depot')),
      depots text[] not null default '{}', created_at timestamptz not null default now());
    create table if not exists entries(
      id serial primary key, entry_date date not null, depot text not null, category text not null,
      orders int not null default 0, delivered int not null default 0,
      stock numeric not null default 0, avg_daily numeric not null default 0,
      updated_by int references users(id) on delete set null, updated_at timestamptz not null default now(),
      unique(entry_date, depot, category));
    create table if not exists reports(
      report_date date primary key, head text not null default '',
      today_sales numeric not null default 0, today_target numeric not null default 0,
      mtd_sales numeric not null default 0, mtd_target numeric not null default 0,
      issues text not null default '');
    alter table users add column if not exists mobile text not null default '';
    alter table users add column if not exists designation text not null default '';
    alter table users add column if not exists must_change boolean not null default false;
    create table if not exists reset_requests(
      id serial primary key, user_id int not null unique references users(id) on delete cascade,
      mobile text not null default '', mobile_match boolean not null default false, created_at timestamptz not null default now());`);
  const c = await pool.query('select count(*)::int n from users');
  if (c.rows[0].n === 0) {
    const u = process.env.ADMIN_USERNAME || 'admin', p = process.env.ADMIN_PASSWORD;
    if (!p || p.length < 8) { console.error('ADMIN_PASSWORD (minimum 8 character) set korun'); process.exit(1); }
    await pool.query('insert into users(username,password_hash,name,role) values($1,$2,$3,$4)',
      [lc(u), await bcrypt.hash(p, 10), 'Admin', 'admin']);
    console.log('First admin toiri hoyeche:', lc(u));
  }
}

// ---------- auth ----------
const fails = new Map();
const auth = wrap(async (req, res, next) => {
  if (!req.session.uid) return res.status(401).json({ error: 'Login korun' });
  const r = await pool.query('select id,username,name,role,depots,mobile,designation,must_change from users where id=$1', [req.session.uid]);
  if (!r.rows[0]) { req.session = null; return res.status(401).json({ error: 'Login korun' }); }
  req.user = r.rows[0];
  if (req.user.must_change && !['/api/me', '/api/password'].includes(req.originalUrl.split('?')[0]))
    return res.status(403).json({ error: 'Age password change korun', must_change: true });
  next();
});
const admin = (req, res, next) => req.user.role === 'admin' ? next() : res.status(403).json({ error: 'Shudhu admin' });
async function allDepots() {
  const r = await pool.query('select distinct unnest(depots) d from users order by 1');
  return r.rows.map(x => x.d);
}
async function allowedDepots(u) { return u.role === 'admin' ? allDepots() : u.depots; }
const findDepot = (list, d) => list.find(x => lc(x) === lc(d));

app.get('/healthz', (req, res) => res.send('ok'));

app.post('/api/login', wrap(async (req, res) => {
  const username = lc(req.body.username), password = String(req.body.password || '');
  const key = req.ip + '|' + username, f = fails.get(key);
  if (f && f.n >= 8 && Date.now() - f.t < 15 * 60 * 1000) return res.status(429).json({ error: 'Onek bar vul. 15 minute pore chesta korun.' });
  const r = await pool.query('select * from users where username=$1', [username]);
  const ok = r.rows[0] && await bcrypt.compare(password, r.rows[0].password_hash);
  if (!ok) { fails.set(key, { n: (f && Date.now() - f.t < 15 * 60 * 1000 ? f.n : 0) + 1, t: Date.now() }); return res.status(401).json({ error: 'Username ba password vul' }); }
  fails.delete(key);
  req.session = { uid: r.rows[0].id };
  res.json({ ok: true });
}));
app.post('/api/logout', (req, res) => { req.session = null; res.json({ ok: true }); });
app.get('/api/me', auth, (req, res) => res.json(req.user));
app.get('/api/depots', auth, wrap(async (req, res) => res.json({ depots: await allDepots() })));

// ---------- entries ----------
app.get('/api/entries', auth, wrap(async (req, res) => {
  const d = String(req.query.date || '');
  if (!DATE.test(d)) return res.status(400).json({ error: 'Date thik nai' });
  const r = await pool.query(`select to_char(e.entry_date,'YYYY-MM-DD') date, e.depot, e.category, e.orders, e.delivered,
    e.stock::float8 stock, e.avg_daily::float8 avg_daily, e.updated_at, u.name "by"
    from entries e left join users u on u.id=e.updated_by where e.entry_date=$1 order by e.category, e.depot`, [d]);
  res.json({ entries: r.rows });
}));
app.put('/api/entries', auth, wrap(async (req, res) => {
  const b = req.body, date = String(b.date || '');
  if (!DATE.test(date)) return res.status(400).json({ error: 'Date thik nai' });
  if (!CATS.includes(b.category)) return res.status(400).json({ error: 'Category thik nai' });
  const depot = findDepot(await allowedDepots(req.user), b.depot);
  if (!depot) return res.status(403).json({ error: 'Ei depot e data dewar anumoti nai' });
  const orders = nonNeg(b.orders), delivered = nonNeg(b.delivered), stock = nonNeg(b.stock), avg = nonNeg(b.avg_daily);
  if ([orders, delivered, stock, avg].includes(null)) return res.status(400).json({ error: 'Number gula thik nai' });
  if (delivered > orders) return res.status(400).json({ error: 'Delivered, Orders er cheye beshi hote pare na' });
  await pool.query(`insert into entries(entry_date,depot,category,orders,delivered,stock,avg_daily,updated_by,updated_at)
    values($1,$2,$3,$4,$5,$6,$7,$8,now())
    on conflict(entry_date,depot,category) do update set orders=$4,delivered=$5,stock=$6,avg_daily=$7,updated_by=$8,updated_at=now()`,
    [date, depot, b.category, Math.round(orders), Math.round(delivered), stock, avg, req.user.id]);
  res.json({ ok: true });
}));
app.delete('/api/entries', auth, wrap(async (req, res) => {
  const { date, depot, category } = req.query;
  if (!DATE.test(String(date || ''))) return res.status(400).json({ error: 'Date thik nai' });
  const d = findDepot(await allowedDepots(req.user), depot);
  if (!d) return res.status(403).json({ error: 'Anumoti nai' });
  await pool.query('delete from entries where entry_date=$1 and depot=$2 and category=$3', [date, d, category]);
  res.json({ ok: true });
}));

// ---------- sales & issues report (admin write) ----------
app.get('/api/report', auth, wrap(async (req, res) => {
  const d = String(req.query.date || '');
  if (!DATE.test(d)) return res.status(400).json({ error: 'Date thik nai' });
  const r = await pool.query(`select head, today_sales::float8, today_target::float8, mtd_sales::float8, mtd_target::float8, issues
    from reports where report_date=$1`, [d]);
  res.json({ report: r.rows[0] || { head: '', today_sales: 0, today_target: 0, mtd_sales: 0, mtd_target: 0, issues: '' } });
}));
app.put('/api/report', auth, admin, wrap(async (req, res) => {
  const b = req.body, date = String(b.date || '');
  if (!DATE.test(date)) return res.status(400).json({ error: 'Date thik nai' });
  const n = [b.today_sales, b.today_target, b.mtd_sales, b.mtd_target].map(v => nonNeg(v || 0));
  if (n.includes(null)) return res.status(400).json({ error: 'Number gula thik nai' });
  await pool.query(`insert into reports(report_date,head,today_sales,today_target,mtd_sales,mtd_target,issues) values($1,$2,$3,$4,$5,$6,$7)
    on conflict(report_date) do update set head=$2,today_sales=$3,today_target=$4,mtd_sales=$5,mtd_target=$6,issues=$7`,
    [date, String(b.head || '').slice(0, 100), ...n, String(b.issues || '').slice(0, 5000)]);
  res.json({ ok: true });
}));

// ---------- user management (admin) ----------
const USERNAME = /^[a-z0-9_.-]{3,30}$/;
app.get('/api/users', auth, admin, wrap(async (req, res) => {
  const r = await pool.query('select id,username,name,role,depots from users order by role, name, username');
  res.json({ users: r.rows });
}));
app.post('/api/users', auth, admin, wrap(async (req, res) => {
  const b = req.body, username = lc(b.username), password = String(b.password || '');
  if (!USERNAME.test(username)) return res.status(400).json({ error: 'ID/Username: 3-30 ta english letter/number/._-' });
  if (password.length < 8) return res.status(400).json({ error: 'Password minimum 8 character' });
  const mobile = normMobile(b.mobile);
  if (mobile === null) return res.status(400).json({ error: 'Mobile number thik nai' });
  const role = b.role === 'admin' ? 'admin' : 'depot';
  try {
    await pool.query('insert into users(username,password_hash,name,role,depots,mobile,designation,must_change) values($1,$2,$3,$4,$5,$6,$7,true)',
      [username, await bcrypt.hash(password, 10), String(b.name || '').slice(0, 80), role, cleanDepots(b.depots), mobile, String(b.designation || '').slice(0, 80)]);
  } catch (e) { if (e.code === '23505') return res.status(409).json({ error: 'Ei ID ageii ache' }); throw e; }
  res.json({ ok: true });
}));
app.put('/api/users/:id', auth, admin, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10), b = req.body;
  if (!id) return res.status(400).json({ error: 'ID thik nai' });
  const role = b.role === 'admin' ? 'admin' : 'depot';
  if (id === req.user.id && role !== 'admin') return res.status(400).json({ error: 'Nijer admin role bondho kora jabe na' });
  const mobile = normMobile(b.mobile);
  if (mobile === null) return res.status(400).json({ error: 'Mobile number thik nai' });
  const pw = String(b.password || '');
  if (pw && pw.length < 8) return res.status(400).json({ error: 'Password minimum 8 character' });
  await pool.query('update users set name=$1, role=$2, depots=$3, mobile=$4, designation=$5 where id=$6',
    [String(b.name || '').slice(0, 80), role, cleanDepots(b.depots), mobile, String(b.designation || '').slice(0, 80), id]);
  if (pw) {
    await pool.query('update users set password_hash=$1, must_change=$2 where id=$3', [await bcrypt.hash(pw, 10), id !== req.user.id, id]);
    await pool.query('delete from reset_requests where user_id=$1', [id]);
  }
  res.json({ ok: true });
}));
app.delete('/api/users/:id', auth, admin, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (id === req.user.id) return res.status(400).json({ error: 'Nijeke delete kora jabe na' });
  await pool.query('delete from users where id=$1', [id]);
  res.json({ ok: true });
}));

// ---------- password: change, forgot, reset requests ----------
app.post('/api/password', auth, wrap(async (req, res) => {
  const cur = String(req.body.current || ''), nw = String(req.body.password || '');
  if (nw.length < 8) return res.status(400).json({ error: 'Notun password minimum 8 character' });
  if (nw === cur) return res.status(400).json({ error: 'Notun password ager ta theke alada hote hobe' });
  const r = await pool.query('select password_hash from users where id=$1', [req.user.id]);
  if (!(await bcrypt.compare(cur, r.rows[0].password_hash))) return res.status(400).json({ error: 'Ager password vul' });
  await pool.query('update users set password_hash=$1, must_change=false where id=$2', [await bcrypt.hash(nw, 10), req.user.id]);
  res.json({ ok: true });
}));
const fAttempts = new Map();
app.post('/api/forgot', wrap(async (req, res) => {
  const k = req.ip, a = fAttempts.get(k);
  if (a && a.n >= 5 && Date.now() - a.t < 60 * 60 * 1000) return res.status(429).json({ error: 'Onek bar request. Pore chesta korun.' });
  fAttempts.set(k, { n: (a && Date.now() - a.t < 60 * 60 * 1000 ? a.n : 0) + 1, t: a && Date.now() - a.t < 60 * 60 * 1000 ? a.t : Date.now() });
  const username = lc(req.body.username), mobile = normMobile(req.body.mobile);
  if (!username || !mobile) return res.status(400).json({ error: 'ID ar mobile number dewa dorkar' });
  const u = (await pool.query('select id, mobile from users where username=$1', [username])).rows[0];
  if (u) {
    const match = !!u.mobile && last10(u.mobile) === last10(mobile);
    await pool.query(`insert into reset_requests(user_id,mobile,mobile_match) values($1,$2,$3)
      on conflict(user_id) do update set mobile=$2, mobile_match=$3, created_at=now()`, [u.id, mobile, match]);
  }
  // User ache kina seta janano hoy na (security)
  res.json({ ok: true });
}));
app.get('/api/resets', auth, admin, wrap(async (req, res) => {
  const r = await pool.query(`select q.id, u.username, u.name, u.mobile registered, q.mobile submitted, q.mobile_match, q.created_at
    from reset_requests q join users u on u.id=q.user_id order by q.created_at desc`);
  res.json({ resets: r.rows });
}));
app.delete('/api/resets/:id', auth, admin, wrap(async (req, res) => {
  await pool.query('delete from reset_requests where id=$1', [parseInt(req.params.id, 10) || 0]);
  res.json({ ok: true });
}));

app.use(express.static(path.join(__dirname, 'public')));
app.use((err, req, res, next) => { console.error(err); res.status(500).json({ error: 'Server error' }); });

init().then(() => app.listen(process.env.PORT || 3000, () => console.log('Running on', process.env.PORT || 3000)))
  .catch(e => { console.error(e); process.exit(1); });
