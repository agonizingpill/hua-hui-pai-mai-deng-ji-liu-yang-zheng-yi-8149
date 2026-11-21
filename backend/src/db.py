import os
from sqlalchemy import create_engine,text
from sqlalchemy.orm import DeclarativeBase,sessionmaker
URL=os.environ['DATABASE_URL']
OPTIONS={} if URL.startswith('sqlite') else {'isolation_level':'READ COMMITTED'}
if URL.startswith('sqlite'):OPTIONS['connect_args']={'check_same_thread':False}
engine=create_engine(URL,pool_pre_ping=True,**OPTIONS)
Session=sessionmaker(engine,expire_on_commit=False)
class Base(DeclarativeBase):pass
def session():
    with Session() as s:yield s
def migrate():
    Base.metadata.create_all(engine)
    if engine.dialect.name!='postgresql':return
    columns={
        'auction_lots':[
            'allocated_crates INTEGER',
            'pending_crates INTEGER NOT NULL DEFAULT 0',
            'shortfall_crates INTEGER NOT NULL DEFAULT 0',
            "disposition VARCHAR(30) NOT NULL DEFAULT 'offered'",
            "disposition_reason VARCHAR(450) NOT NULL DEFAULT ''",
            'receipt_check_id INTEGER REFERENCES receipt_checks(id)',
        ],
        'deals':[
            "state VARCHAR(20) NOT NULL DEFAULT 'active'",
            'delivered_crates INTEGER',
            'shortfall_crates INTEGER NOT NULL DEFAULT 0',
            'shortfall_choice VARCHAR(20)',
            'shortfall_note VARCHAR(250)',
            'choice_at VARCHAR(20)',
            'choice_by VARCHAR(100)',
            'receipt_check_id INTEGER REFERENCES receipt_checks(id)',
            'snapshot JSON',
        ],
        'settlements':[
            'crates INTEGER',
            'receipt_check_id INTEGER REFERENCES receipt_checks(id)',
        ],
    }
    with engine.begin() as c:
        for table,defs in columns.items():
            for ddl in defs:c.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {ddl}'))
        c.execute(text("UPDATE auction_lots SET disposition=CASE state WHEN 'sold' THEN 'sold' WHEN 'cancelled' THEN 'cancelled' ELSE 'offered' END WHERE disposition='offered' AND state IN ('sold','cancelled')"))
        c.execute(text("UPDATE auction_lots SET allocated_crates=crates WHERE allocated_crates IS NULL"))
        c.execute(text("UPDATE deals SET state='active' WHERE state IS NULL"))
        c.execute(text("UPDATE deals SET delivered_crates=crates WHERE delivered_crates IS NULL"))
