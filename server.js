'use strict';
const express = require('express');
const { Pool } = require('pg');
const bcrypt = require('bcryptjs');
const cookieSession = require('cookie-session');
const path = require('path');

const prod = process.env.NODE_ENV === 'production';
if (!process.env.DATABASE_URL) { console.error('DATABASE_URL is not set'); process.exit(1); }
if (prod && !process.env.SESSION_SECRET) { console.error('SESSION_SECRET is not set'); process.exit(1); }

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.PGSSL === 'false' ? false : (prod ? { rejectUnauthorized: false } : false)
});

const app = express();
app.set('trust proxy', 1);

// Lightweight health check & keep-alive ping endpoints (Zero DB load, fast HTTP 200)
app.get(['/healthz', '/ping', '/api/health'], (req, res) => {
  res.status(200).json({ status: 'ok', uptime: Math.floor(process.uptime()), timestamp: Date.now() });
});
app.head(['/healthz', '/ping', '/api/health'], (req, res) => {
  res.status(200).end();
});

app.use(express.json({ limit: '2mb' }));
app.use(cookieSession({
  name: 'dup', keys: [process.env.SESSION_SECRET || 'dev-only-secret'],
  maxAge: 12 * 60 * 60 * 1000, httpOnly: true, sameSite: 'lax', secure: prod
}));

// Mutating requests must be JSON (basic CSRF protection along with sameSite cookie)
app.use((req, res, next) => {
  if (['POST', 'PUT', 'DELETE'].includes(req.method) && !req.is('application/json') && req.path.startsWith('/api/') && req.method !== 'DELETE')
    return res.status(415).json({ error: 'JSON payload required' });
  next();
});

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
    create table if not exists categories(
      id serial primary key, name text unique not null, display_order int not null default 0,
      created_at timestamptz not null default now());
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
      mobile text not null default '', mobile_match boolean not null default false, created_at timestamptz not null default now());
    create table if not exists depot_vehicles(
      id serial primary key, entry_date date not null, depot text not null,
      total_vehicles int not null default 0, dispatched int not null default 0,
      on_road int not null default 0, delivered int not null default 0,
      notes text not null default '', updated_by int references users(id) on delete set null,
      updated_at timestamptz not null default now(), unique(entry_date, depot));
    alter table reports add column if not exists top_depot text not null default '';
    alter table reports add column if not exists low_depot text not null default '';
    alter table categories add column if not exists unit text not null default 'Qty';
    alter table users add column if not exists categories text[] not null default '{}';
    alter table depot_vehicles add column if not exists under_maintenance int not null default 0;
    create table if not exists vehicle_dispatches(
      id serial primary key, entry_date date not null, depot text not null,
      vehicle_no text not null, dispatch_time text not null default '',
      driver_name text not null default '', destination text not null default '',
      status text not null default 'dispatched', notes text not null default '',
      created_by int references users(id) on delete set null, created_at timestamptz not null default now());
    create table if not exists registered_vans(
      id serial primary key, depot text not null,
      vehicle_no text not null, driver_name text not null default '',
      driver_mobile text not null default '', vehicle_type text not null default 'Pickup Van',
      status text not null default 'active', created_at timestamptz not null default now(),
      unique(depot, vehicle_no));
  `);

  // Unblock any users previously forced into must_change
  await pool.query('update users set must_change = false where must_change = true');

  // Categories initialization with standard Units of Measurement (UoM)
  const defaultCats = [
    { name: 'Frozen', unit: 'Pkt' },
    { name: 'Chicken', unit: 'Kg' },
    { name: 'Egg', unit: 'Pcs' },
    { name: 'Dairy', unit: 'Ltr' },
    { name: 'Tea', unit: 'Kg' },
    { name: 'Sweets', unit: 'Kg' },
    { name: 'Others', unit: 'Pcs' }
  ];
  for (let i = 0; i < defaultCats.length; i++) {
    await pool.query(`
      insert into categories(name, display_order, unit)
      values($1, $2, $3)
      on conflict (name) do update set unit = $3
    `, [defaultCats[i].name, (i + 1) * 10, defaultCats[i].unit]);
  }
  console.log('Categories & units initialized');

  // First admin initialization
  const c = await pool.query('select count(*)::int n from users');
  if (c.rows[0].n === 0) {
    const u = process.env.ADMIN_USERNAME || 'admin', p = process.env.ADMIN_PASSWORD;
    if (!p || p.length < 8) { console.error('ADMIN_PASSWORD (minimum 8 characters) is required'); process.exit(1); }
    await pool.query('insert into users(username,password_hash,name,role,must_change) values($1,$2,$3,$4,false)',
      [lc(u), await bcrypt.hash(p, 10), 'Admin', 'admin']);
    console.log('First admin user created:', lc(u));
  }
}

async function getAllCategories() {
  const r = await pool.query('select name from categories order by display_order, id');
  return r.rows.map(x => x.name);
}

// ---------- auth ----------
const fails = new Map();
const auth = wrap(async (req, res, next) => {
  if (!req.session.uid) return res.status(401).json({ error: 'Please login to continue' });
  const r = await pool.query('select id,username,name,role,depots,categories,mobile,designation,must_change from users where id=$1', [req.session.uid]);
  if (!r.rows[0]) { req.session = null; return res.status(401).json({ error: 'Please login to continue' }); }
  req.user = r.rows[0];
  next();
});
const admin = (req, res, next) => req.user.role === 'admin' ? next() : res.status(403).json({ error: 'Admin access required' });
async function allDepots() {
  const r = await pool.query('select distinct unnest(depots) d from users order by 1');
  return r.rows.map(x => x.d);
}
async function allowedDepots(u) { return u.role === 'admin' ? allDepots() : (u.depots || []); }
async function allowedCategories(u) {
  const all = await getAllCategories();
  if (u.role === 'admin') return all;
  if (!u.categories || !u.categories.length) return all;
  const filtered = all.filter(c => u.categories.some(uc => lc(uc) === lc(c)));
  return filtered.length ? filtered : all;
}
const findDepot = (list, d) => list.find(x => lc(x) === lc(d));

app.get('/healthz', (req, res) => res.send('ok'));

app.post('/api/login', wrap(async (req, res) => {
  const username = lc(String(req.body.username || '').trim());
  const password = String(req.body.password || '').trim();
  if (!username || !password) return res.status(400).json({ error: 'Please enter both Username and Password' });

  const key = req.ip + '|' + username, f = fails.get(key);
  if (f && f.n >= 10 && Date.now() - f.t < 15 * 60 * 1000) {
    return res.status(429).json({ error: 'Too many failed login attempts. Please try again after 15 minutes.' });
  }

  const r = await pool.query('select * from users where lower(trim(username)) = lower(trim($1))', [username]);
  const ok = r.rows[0] && await bcrypt.compare(password, r.rows[0].password_hash);
  if (!ok) {
    fails.set(key, { n: (f && Date.now() - f.t < 15 * 60 * 1000 ? f.n : 0) + 1, t: Date.now() });
    return res.status(401).json({ error: 'Invalid username or password' });
  }

  fails.delete(key);
  await pool.query('update users set must_change=false where id=$1', [r.rows[0].id]);
  req.session = { uid: r.rows[0].id };
  res.json({ ok: true });
}));

app.post('/api/logout', (req, res) => { req.session = null; res.json({ ok: true }); });
app.get('/api/me', auth, (req, res) => res.json(req.user));
app.get('/api/depots', auth, wrap(async (req, res) => res.json({ depots: await allDepots() })));

// ---------- categories management (dynamic) ----------
app.get('/api/categories', auth, wrap(async (req, res) => {
  const r = await pool.query('select id, name, display_order, coalesce(unit, \'Qty\') as unit from categories order by display_order, id');
  res.json({ categories: r.rows.map(x => x.name), details: r.rows });
}));

app.post('/api/categories', auth, admin, wrap(async (req, res) => {
  const name = String(req.body.name || '').trim();
  const unit = String(req.body.unit || 'Qty').trim().slice(0, 20);
  if (!name || name.length > 50) return res.status(400).json({ error: 'Category name is required (max 50 characters)' });
  try {
    const maxOrderRes = await pool.query('select coalesce(max(display_order), 0) + 10 as next_order from categories');
    const nextOrder = maxOrderRes.rows[0].next_order;
    await pool.query('insert into categories(name, display_order, unit) values($1, $2, $3)', [name, nextOrder, unit]);
    res.json({ ok: true, name, unit });
  } catch (e) {
    if (e.code === '23505') return res.status(409).json({ error: 'Category name already exists' });
    throw e;
  }
}));

app.delete('/api/categories/:name', auth, admin, wrap(async (req, res) => {
  const name = String(req.params.name || '').trim();
  if (!name) return res.status(400).json({ error: 'Category name required' });
  const countRes = await pool.query('select count(*)::int n from entries where lower(category)=lower($1)', [name]);
  if (countRes.rows[0].n > 0) {
    return res.status(400).json({ error: `Cannot delete: ${countRes.rows[0].n} entries exist for this category.` });
  }
  await pool.query('delete from categories where lower(name)=lower($1)', [name]);
  res.json({ ok: true });
}));

// ---------- entries ----------
app.get('/api/entries', auth, wrap(async (req, res) => {
  const d = String(req.query.date || '');
  if (!DATE.test(d)) return res.status(400).json({ error: 'Invalid date format' });
  
  let query = `select to_char(e.entry_date,'YYYY-MM-DD') date, e.depot, e.category, e.orders, e.delivered,
    e.stock::float8 stock, e.avg_daily::float8 avg_daily, e.updated_at, u.name "by"
    from entries e left join users u on u.id=e.updated_by where e.entry_date=$1`;
  const params = [d];
  
  if (req.user.role !== 'admin') {
    const allowedD = (await allowedDepots(req.user)).map(lc);
    const allowedC = (await allowedCategories(req.user)).map(lc);
    query += ` and lower(e.depot) = any($2) and lower(e.category) = any($3)`;
    params.push(allowedD, allowedC);
  }
  
  query += ` order by e.category, e.depot`;
  const r = await pool.query(query, params);
  res.json({ entries: r.rows });
}));

app.put('/api/entries', auth, wrap(async (req, res) => {
  const b = req.body, date = String(b.date || '');
  if (!DATE.test(date)) return res.status(400).json({ error: 'Invalid date format' });
  
  const depot = findDepot(await allowedDepots(req.user), b.depot);
  if (!depot) return res.status(403).json({ error: 'You are not authorized for this depot' });

  const allowedCats = await allowedCategories(req.user);
  const cat = allowedCats.find(c => lc(c) === lc(b.category));
  if (!cat) return res.status(403).json({ error: `You are not authorized for product category: ${b.category}` });

  const orders = nonNeg(b.orders), delivered = nonNeg(b.delivered), stock = nonNeg(b.stock), avg = nonNeg(b.avg_daily);
  if ([orders, delivered, stock, avg].includes(null)) return res.status(400).json({ error: 'Invalid numerical values' });
  if (delivered > orders) return res.status(400).json({ error: 'Delivered quantity cannot exceed Orders' });
  await pool.query(`insert into entries(entry_date,depot,category,orders,delivered,stock,avg_daily,updated_by,updated_at)
    values($1,$2,$3,$4,$5,$6,$7,$8,now())
    on conflict(entry_date,depot,category) do update set orders=$4,delivered=$5,stock=$6,avg_daily=$7,updated_by=$8,updated_at=now()`,
    [date, depot, cat, Math.round(orders), Math.round(delivered), stock, avg, req.user.id]);
  res.json({ ok: true });
}));

app.delete('/api/entries', auth, wrap(async (req, res) => {
  const { date, depot, category } = req.query;
  if (!DATE.test(String(date || ''))) return res.status(400).json({ error: 'Invalid date format' });
  const d = findDepot(await allowedDepots(req.user), depot);
  if (!d) return res.status(403).json({ error: 'Unauthorized depot' });
  const allowedCats = await allowedCategories(req.user);
  if (!allowedCats.some(c => lc(c) === lc(category))) return res.status(403).json({ error: 'Unauthorized category' });
  await pool.query('delete from entries where entry_date=$1 and depot=$2 and category=$3', [date, d, category]);
  res.json({ ok: true });
}));

// ---------- bulk entries (Stock from Poloxy / Orders / Combined) ----------
app.post('/api/bulk/entries', auth, wrap(async (req, res) => {
  const { date, items } = req.body;
  if (!DATE.test(String(date || ''))) return res.status(400).json({ error: 'Invalid date format' });
  if (!Array.isArray(items) || !items.length) return res.status(400).json({ error: 'Data list is empty' });

  const allowed = await allowedDepots(req.user);
  const allCats = await getAllCategories();
  let updated = 0, skipped = 0;

  for (const it of items) {
    const depot = findDepot(allowed, it.depot);
    if (!depot) { skipped++; continue; }
    const cat = allCats.find(c => lc(c) === lc(it.category));
    if (!cat) { skipped++; continue; }

    const o = it.orders != null && it.orders !== '' ? nonNeg(it.orders) : null;
    const v = it.delivered != null && it.delivered !== '' ? nonNeg(it.delivered) : null;
    const s = it.stock != null && it.stock !== '' ? nonNeg(it.stock) : null;
    const a = it.avg_daily != null && it.avg_daily !== '' ? nonNeg(it.avg_daily) : null;

    await pool.query(`
      insert into entries(entry_date, depot, category, orders, delivered, stock, avg_daily, updated_by, updated_at)
      values($1, $2, $3, coalesce($4, 0), coalesce($5, 0), coalesce($6, 0), coalesce($7, 0), $8, now())
      on conflict(entry_date, depot, category) do update set
        orders = coalesce($4, entries.orders),
        delivered = coalesce($5, entries.delivered),
        stock = coalesce($6, entries.stock),
        avg_daily = coalesce($7, entries.avg_daily),
        updated_by = $8,
        updated_at = now()
    `, [date, depot, cat, o != null ? Math.round(o) : null, v != null ? Math.round(v) : null, s, a, req.user.id]);
    updated++;
  }
  res.json({ ok: true, updated, skipped });
}));

// ---------- vehicles tracking ----------
app.get('/api/vehicles', auth, wrap(async (req, res) => {
  const d = String(req.query.date || '');
  if (!DATE.test(d)) return res.status(400).json({ error: 'Invalid date format' });
  const r = await pool.query(`
    select to_char(v.entry_date,'YYYY-MM-DD') date, v.depot, v.total_vehicles, v.dispatched,
      v.on_road, v.delivered, coalesce(v.under_maintenance, 0) as under_maintenance, v.notes, v.updated_at, u.name "by"
    from depot_vehicles v left join users u on u.id=v.updated_by
    where v.entry_date=$1 order by v.depot
  `, [d]);
  res.json({ vehicles: r.rows });
}));

app.put('/api/vehicles', auth, wrap(async (req, res) => {
  const b = req.body, date = String(b.date || '');
  if (!DATE.test(date)) return res.status(400).json({ error: 'Invalid date format' });
  const depot = findDepot(await allowedDepots(req.user), b.depot);
  if (!depot) return res.status(403).json({ error: 'You are not authorized for this depot' });

  const total = nonNeg(b.total_vehicles) || 0;
  const dispatched = nonNeg(b.dispatched) || 0;
  const delivered = nonNeg(b.delivered) || 0;
  const maint = nonNeg(b.under_maintenance) || 0;
  const on_road = b.on_road != null && b.on_road !== '' ? (nonNeg(b.on_road) || 0) : Math.max(0, Math.round(total) - Math.round(delivered) - Math.round(maint));
  const notes = String(b.notes || '').slice(0, 500);

  await pool.query(`
    insert into depot_vehicles(entry_date, depot, total_vehicles, dispatched, on_road, delivered, under_maintenance, notes, updated_by, updated_at)
    values($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
    on conflict(entry_date, depot) do update set
      total_vehicles=$3, dispatched=$4, on_road=$5, delivered=$6, under_maintenance=$7, notes=$8, updated_by=$9, updated_at=now()
  `, [date, depot, Math.round(total), Math.round(dispatched), Math.round(on_road), Math.round(delivered), Math.round(maint), notes, req.user.id]);
  res.json({ ok: true });
}));

// ---------- individual vehicle dispatch logs ----------
app.get('/api/dispatches', auth, wrap(async (req, res) => {
  const d = String(req.query.date || '');
  if (!DATE.test(d)) return res.status(400).json({ error: 'Invalid date format' });
  let query = `select vd.id, to_char(vd.entry_date,'YYYY-MM-DD') date, vd.depot, vd.vehicle_no, vd.dispatch_time,
    vd.driver_name, vd.destination, vd.status, vd.notes, vd.created_at, u.name "by"
    from vehicle_dispatches vd left join users u on u.id=vd.created_by
    where vd.entry_date=$1`;
  const params = [d];
  if (req.user.role !== 'admin') {
    const allowedD = (await allowedDepots(req.user)).map(lc);
    query += ` and lower(vd.depot) = any($2)`;
    params.push(allowedD);
  }
  query += ` order by vd.depot, vd.dispatch_time, vd.id`;
  const r = await pool.query(query, params);
  res.json({ dispatches: r.rows });
}));

app.post('/api/dispatches', auth, wrap(async (req, res) => {
  const b = req.body, date = String(b.date || '');
  if (!DATE.test(date)) return res.status(400).json({ error: 'Invalid date format' });
  const depot = findDepot(await allowedDepots(req.user), b.depot);
  if (!depot) return res.status(403).json({ error: 'You are not authorized for this depot' });
  const vehicle_no = String(b.vehicle_no || '').trim().slice(0, 50);
  if (!vehicle_no) return res.status(400).json({ error: 'Vehicle registration number is required' });
  const dispatch_time = String(b.dispatch_time || '').trim().slice(0, 30);
  const driver_name = String(b.driver_name || '').trim().slice(0, 100);
  const destination = String(b.destination || '').trim().slice(0, 150);
  const status = String(b.status || 'dispatched').trim().slice(0, 30);
  const notes = String(b.notes || '').trim().slice(0, 300);

  const r = await pool.query(`
    insert into vehicle_dispatches(entry_date, depot, vehicle_no, dispatch_time, driver_name, destination, status, notes, created_by)
    values($1, $2, $3, $4, $5, $6, $7, $8, $9) returning id
  `, [date, depot, vehicle_no, dispatch_time, driver_name, destination, status, notes, req.user.id]);

  // Sync count in depot_vehicles
  const countRes = await pool.query('select count(*)::int n from vehicle_dispatches where entry_date=$1 and lower(depot)=lower($2)', [date, depot]);
  await pool.query(`
    insert into depot_vehicles(entry_date, depot, dispatched, updated_by, updated_at)
    values($1, $2, $3, $4, now())
    on conflict(entry_date, depot) do update set dispatched = greatest(depot_vehicles.dispatched, $3), updated_at = now()
  `, [date, depot, countRes.rows[0].n, req.user.id]);

  res.json({ ok: true, id: r.rows[0].id });
}));

app.delete('/api/dispatches/:id', auth, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (!id) return res.status(400).json({ error: 'Invalid dispatch ID' });
  const dRes = await pool.query('select * from vehicle_dispatches where id=$1', [id]);
  if (!dRes.rows[0]) return res.status(404).json({ error: 'Dispatch record not found' });
  const depot = findDepot(await allowedDepots(req.user), dRes.rows[0].depot);
  if (!depot) return res.status(403).json({ error: 'Unauthorized' });
  await pool.query('delete from vehicle_dispatches where id=$1', [id]);
  res.json({ ok: true });
}));

// ---------- registered vans (fleet enlistment by depot) ----------
app.get('/api/registered-vans', auth, wrap(async (req, res) => {
  let query = `select id, depot, vehicle_no, driver_name, driver_mobile, vehicle_type, status, created_at from registered_vans`;
  const params = [];
  if (req.user.role !== 'admin') {
    const allowedD = (await allowedDepots(req.user)).map(lc);
    query += ` where lower(depot) = any($1)`;
    params.push(allowedD);
  } else if (req.query.depot) {
    query += ` where lower(depot) = lower($1)`;
    params.push(String(req.query.depot).trim());
  }
  query += ` order by depot, vehicle_no`;
  const r = await pool.query(query, params);
  res.json({ vans: r.rows });
}));

app.post('/api/registered-vans', auth, admin, wrap(async (req, res) => {
  const b = req.body;
  const depot = String(b.depot || '').trim();
  const vehicle_no = String(b.vehicle_no || '').trim().toUpperCase();
  if (!depot) return res.status(400).json({ error: 'Depot is required' });
  if (!vehicle_no) return res.status(400).json({ error: 'Vehicle registration number is required' });
  const driver_name = String(b.driver_name || '').trim().slice(0, 100);
  const driver_mobile = String(b.driver_mobile || '').trim().slice(0, 30);
  const vehicle_type = String(b.vehicle_type || 'Pickup Van').trim().slice(0, 50);
  const status = String(b.status || 'active').trim().slice(0, 30);

  const r = await pool.query(`
    insert into registered_vans(depot, vehicle_no, driver_name, driver_mobile, vehicle_type, status)
    values($1, $2, $3, $4, $5, $6)
    on conflict(depot, vehicle_no) do update set
      driver_name = excluded.driver_name,
      driver_mobile = excluded.driver_mobile,
      vehicle_type = excluded.vehicle_type,
      status = excluded.status
    returning id
  `, [depot, vehicle_no, driver_name, driver_mobile, vehicle_type, status]);
  res.json({ ok: true, id: r.rows[0].id });
}));

app.delete('/api/registered-vans/:id', auth, admin, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (!id) return res.status(400).json({ error: 'Invalid van ID' });
  await pool.query('delete from registered_vans where id=$1', [id]);
  res.json({ ok: true });
}));

app.post('/api/bulk/registered-vans', auth, admin, wrap(async (req, res) => {
  const { items } = req.body;
  if (!Array.isArray(items) || !items.length) return res.status(400).json({ error: 'No van records provided' });
  let saved = 0, skipped = 0;
  for (const it of items) {
    const depot = String(it.depot || '').trim();
    const vehicle_no = String(it.vehicle_no || it.reg_no || it.vehicle_reg_no || '').trim().toUpperCase();
    if (!depot || !vehicle_no) { skipped++; continue; }
    const driver_name = String(it.driver_name || it.driver || '').trim().slice(0, 100);
    const driver_mobile = String(it.driver_mobile || it.mobile || '').trim().slice(0, 30);
    const vehicle_type = String(it.vehicle_type || it.type || 'Pickup Van').trim().slice(0, 50);
    const status = String(it.status || 'active').trim().slice(0, 30);

    await pool.query(`
      insert into registered_vans(depot, vehicle_no, driver_name, driver_mobile, vehicle_type, status)
      values($1, $2, $3, $4, $5, $6)
      on conflict(depot, vehicle_no) do update set
        driver_name = excluded.driver_name,
        driver_mobile = excluded.driver_mobile,
        vehicle_type = excluded.vehicle_type,
        status = excluded.status
    `, [depot, vehicle_no, driver_name, driver_mobile, vehicle_type, status]);
    saved++;
  }
  res.json({ ok: true, saved, skipped });
}));

app.post('/api/bulk/vehicles', auth, wrap(async (req, res) => {
  const { date, items } = req.body;
  if (!DATE.test(String(date || ''))) return res.status(400).json({ error: 'Invalid date format' });
  if (!Array.isArray(items) || !items.length) return res.status(400).json({ error: 'Data list is empty' });
  const allowed = await allowedDepots(req.user);
  let updated = 0, skipped = 0;
  for (const it of items) {
    const depot = findDepot(allowed, it.depot);
    if (!depot) { skipped++; continue; }
    const total = Math.round(nonNeg(it.total_vehicles) || 0);
    const dispatched = Math.round(nonNeg(it.dispatched) || 0);
    const delivered = Math.round(nonNeg(it.delivered) || 0);
    const maint = Math.round(nonNeg(it.under_maintenance) || 0);
    const on_road = it.on_road != null && it.on_road !== '' ? Math.round(nonNeg(it.on_road) || 0) : Math.max(0, total - delivered - maint);
    const notes = String(it.notes || '').slice(0, 500);
    await pool.query(`
      insert into depot_vehicles(entry_date, depot, total_vehicles, dispatched, on_road, delivered, under_maintenance, notes, updated_by, updated_at)
      values($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
      on conflict(entry_date, depot) do update set
        total_vehicles=$3, dispatched=$4, on_road=$5, delivered=$6, under_maintenance=$7, notes=$8, updated_by=$9, updated_at=now()
    `, [date, depot, total, dispatched, on_road, delivered, maint, notes, req.user.id]);
    updated++;
  }
  res.json({ ok: true, updated, skipped });
}));

// ---------- sales & issues report (admin write) ----------
app.get('/api/report', auth, wrap(async (req, res) => {
  const d = String(req.query.date || '');
  if (!DATE.test(d)) return res.status(400).json({ error: 'Invalid date format' });
  const r = await pool.query(`select head, today_sales::float8, today_target::float8, mtd_sales::float8, mtd_target::float8, top_depot, low_depot, issues
    from reports where report_date=$1`, [d]);
  res.json({ report: r.rows[0] || { head: 'Atikur', today_sales: 0, today_target: 0, mtd_sales: 0, mtd_target: 0, top_depot: '', low_depot: '', issues: '' } });
}));

app.put('/api/report', auth, admin, wrap(async (req, res) => {
  const b = req.body, date = String(b.date || '');
  if (!DATE.test(date)) return res.status(400).json({ error: 'Invalid date format' });
  const n = [b.today_sales, b.today_target, b.mtd_sales, b.mtd_target].map(v => nonNeg(v || 0));
  if (n.includes(null)) return res.status(400).json({ error: 'Invalid numerical values' });
  await pool.query(`insert into reports(report_date,head,today_sales,today_target,mtd_sales,mtd_target,top_depot,low_depot,issues) values($1,$2,$3,$4,$5,$6,$7,$8,$9)
    on conflict(report_date) do update set head=$2,today_sales=$3,today_target=$4,mtd_sales=$5,mtd_target=$6,top_depot=$7,low_depot=$8,issues=$9`,
    [date, String(b.head || 'Atikur').slice(0, 100), ...n, String(b.top_depot || '').slice(0, 100), String(b.low_depot || '').slice(0, 100), String(b.issues || '').slice(0, 5000)]);
  res.json({ ok: true });
}));

// ---------- user management (admin) ----------
const USERNAME = /^[a-z0-9_.-]{3,30}$/;
app.get('/api/users', auth, admin, wrap(async (req, res) => {
  const r = await pool.query('select id,username,name,role,depots,categories,mobile,designation from users order by role, name, username');
  res.json({ users: r.rows });
}));

app.post('/api/users', auth, admin, wrap(async (req, res) => {
  const b = req.body;
  const username = lc(String(b.username || '').trim().replace(/\s+/g, '_'));
  const password = String(b.password || '').trim();

  if (!USERNAME.test(username)) return res.status(400).json({ error: 'User ID must be 3-30 characters (letters, numbers, underscores, dots)' });
  if (password.length < 8) return res.status(400).json({ error: 'Password must be at least 8 characters long' });
  const mobile = normMobile(b.mobile);
  if (mobile === null) return res.status(400).json({ error: 'Invalid mobile number format' });
  const role = b.role === 'admin' ? 'admin' : 'depot';

  try {
    await pool.query('insert into users(username,password_hash,name,role,depots,categories,mobile,designation,must_change) values($1,$2,$3,$4,$5,$6,$7,$8,false)',
      [username, await bcrypt.hash(password, 10), String(b.name || '').slice(0, 80), role, cleanDepots(b.depots), cleanDepots(b.categories), mobile, String(b.designation || '').slice(0, 80)]);
    fails.clear();
  } catch (e) {
    if (e.code === '23505') return res.status(409).json({ error: 'User ID already exists. Please choose a different ID.' });
    throw e;
  }
  res.json({ ok: true });
}));

app.put('/api/users/:id', auth, admin, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10), b = req.body;
  if (!id) return res.status(400).json({ error: 'Invalid user ID' });
  const role = b.role === 'admin' ? 'admin' : 'depot';
  if (id === req.user.id && role !== 'admin') return res.status(400).json({ error: 'Cannot revoke your own admin role' });
  const mobile = normMobile(b.mobile);
  if (mobile === null) return res.status(400).json({ error: 'Invalid mobile number format' });
  const pw = String(b.password || '').trim();
  if (pw && pw.length < 8) return res.status(400).json({ error: 'Password must be at least 8 characters long' });

  await pool.query('update users set name=$1, role=$2, depots=$3, categories=$4, mobile=$5, designation=$6 where id=$7',
    [String(b.name || '').slice(0, 80), role, cleanDepots(b.depots), cleanDepots(b.categories), mobile, String(b.designation || '').slice(0, 80), id]);
  
  if (pw) {
    await pool.query('update users set password_hash=$1, must_change=false where id=$2', [await bcrypt.hash(pw, 10), id]);
    await pool.query('delete from reset_requests where user_id=$1', [id]);
    fails.clear();
  }
  res.json({ ok: true });
}));

app.delete('/api/users/:id', auth, admin, wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (id === req.user.id) return res.status(400).json({ error: 'Cannot delete your own account' });
  await pool.query('delete from users where id=$1', [id]);
  res.json({ ok: true });
}));

// ---------- password: change, forgot, reset requests ----------
app.post('/api/password', auth, wrap(async (req, res) => {
  const cur = String(req.body.current || '').trim(), nw = String(req.body.password || '').trim();
  if (nw.length < 8) return res.status(400).json({ error: 'New password must be at least 8 characters long' });
  if (nw === cur) return res.status(400).json({ error: 'New password must be different from current password' });
  const r = await pool.query('select password_hash from users where id=$1', [req.user.id]);
  if (!(await bcrypt.compare(cur, r.rows[0].password_hash))) return res.status(400).json({ error: 'Incorrect current password' });
  await pool.query('update users set password_hash=$1, must_change=false where id=$2', [await bcrypt.hash(nw, 10), req.user.id]);
  res.json({ ok: true });
}));

const fAttempts = new Map();
app.post('/api/forgot', wrap(async (req, res) => {
  const k = req.ip, a = fAttempts.get(k);
  if (a && a.n >= 5 && Date.now() - a.t < 60 * 60 * 1000) return res.status(429).json({ error: 'Too many requests. Please try again later.' });
  fAttempts.set(k, { n: (a && Date.now() - a.t < 60 * 60 * 1000 ? a.n : 0) + 1, t: a && Date.now() - a.t < 60 * 60 * 1000 ? a.t : Date.now() });
  const username = lc(String(req.body.username || '').trim()), mobile = normMobile(req.body.mobile);
  if (!username || !mobile) return res.status(400).json({ error: 'User ID and mobile number are required' });
  const u = (await pool.query('select id, mobile from users where username=$1', [username])).rows[0];
  if (u) {
    const match = !!u.mobile && last10(u.mobile) === last10(mobile);
    await pool.query(`insert into reset_requests(user_id,mobile,mobile_match) values($1,$2,$3)
      on conflict(user_id) do update set mobile=$2, mobile_match=$3, created_at=now()`, [u.id, mobile, match]);
  }
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
app.use((err, req, res, next) => { console.error(err); res.status(500).json({ error: 'Internal server error' }); });

// Keep-Alive Self-Ping Daemon for Render Free Tier (pings every 10 mins to prevent idle spin-down)
function startKeepAlive() {
  const externalUrl = process.env.RENDER_EXTERNAL_URL || process.env.APP_URL;
  if (!externalUrl) {
    console.log('[Keep-Alive] RENDER_EXTERNAL_URL / APP_URL not set; skipping internal self-ping.');
    return;
  }
  const pingUrl = `${externalUrl.replace(/\/+$/, '')}/ping`;
  const intervalMs = 10 * 60 * 1000; // 10 minutes (Render spins down after 15 mins)

  console.log(`[Keep-Alive] Initialized self-ping daemon targeting ${pingUrl} every 10 minutes`);

  setInterval(async () => {
    try {
      const res = await fetch(pingUrl, {
        headers: { 'User-Agent': 'Render-Self-Ping/1.0' }
      });
      console.log(`[Keep-Alive] Self-ping status: ${res.status}`);
    } catch (err) {
      console.warn(`[Keep-Alive] Self-ping failed:`, err.message);
    }
  }, intervalMs);
}

init().then(() => {
  const port = process.env.PORT || 3000;
  app.listen(port, () => {
    console.log('Running on', port);
    startKeepAlive();
  });
}).catch(e => { console.error(e); process.exit(1); });
