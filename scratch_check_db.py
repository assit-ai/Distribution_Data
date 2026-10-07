import os
import psycopg2

db_url = os.environ.get('DATABASE_URL')
if not db_url and os.path.exists('.env'):
    with open('.env', 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip().startswith('DATABASE_URL='):
                db_url = line.strip().split('=', 1)[1].strip().strip('"').strip("'")

if not db_url:
    # check default in server.js or config
    print("No DATABASE_URL found in .env")
else:
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("""
        SELECT date, depot, category, orders, delivered, stock, total_orders_count, pending_orders 
        FROM entries 
        WHERE date >= '2026-10-05' 
        ORDER BY date, category, depot;
    """)
    rows = cur.fetchall()
    print(f"Total rows found: {len(rows)}")
    for r in rows:
        print(r)
    conn.close()
