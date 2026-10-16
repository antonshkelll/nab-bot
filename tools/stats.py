"""Отчёт по продуктовым метрикам бота из analytics.db.

    python tools/stats.py [дней]     # по умолчанию 30
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analytics import report  # noqa: E402

db = ROOT / "analytics.db"
if not db.exists():
    sys.exit("analytics.db ещё нет: бот создаст его при первом запуске с ANALYTICS=1")
print(report(db, int(sys.argv[1]) if len(sys.argv) > 1 else 30))
