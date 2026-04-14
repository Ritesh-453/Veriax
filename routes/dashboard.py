from flask import Blueprint, current_app
from database.db import get_db
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import base64

dashboard_bp = Blueprint('dashboard', __name__)

def make_chart(labels, values, color, title):
    fig, ax = plt.subplots(figsize=(5, 2.5))
    ax.bar(labels, values, color=color, edgecolor='none', linewidth=0)
    ax.set_title(title, fontsize=10, pad=8)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.yaxis.set_visible(False)
    ax.tick_params(axis='x', labelsize=8)
    for i, v in enumerate(values):
        ax.text(i, v + 0.1, str(v), ha='center', fontsize=8, fontweight='bold')
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, transparent=True)
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def get_dashboard_data(db_path):
    db = get_db(db_path)

    total_assets = db.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
    total_violations = db.execute('SELECT COUNT(*) FROM violations').fetchone()[0]
    scans_today = db.execute(
        "SELECT COUNT(*) FROM violations WHERE DATE(detected_at) = DATE('now')"
    ).fetchone()[0]

    # Critical violations (≥90%)
    critical_count = db.execute(
        "SELECT COUNT(*) FROM violations WHERE similarity >= 90"
    ).fetchone()[0]

    # DMCA notices generated (= violations above 70%)
    dmca_count = db.execute(
        "SELECT COUNT(*) FROM violations WHERE similarity >= 70"
    ).fetchone()[0]

    # Violations per day (last 7 days)
    rows = db.execute('''
        SELECT DATE(detected_at) as day, COUNT(*) as count
        FROM violations
        WHERE detected_at >= DATE('now', '-7 days')
        GROUP BY day ORDER BY day
    ''').fetchall()
    days = [r['day'][-5:] for r in rows]
    counts = [r['count'] for r in rows]

    # Top violated assets
    top = db.execute('''
        SELECT a.name, COUNT(v.id) as total
        FROM violations v
        JOIN assets a ON v.asset_id = a.id
        GROUP BY a.name
        ORDER BY total DESC LIMIT 5
    ''').fetchall()
    top_names = [r['name'][:15] for r in top]
    top_counts = [r['total'] for r in top]

    db.close()

    chart1 = make_chart(days, counts, '#4f46e5', 'Violations (last 7 days)') if days else None
    chart2 = make_chart(top_names, top_counts, '#e11d48', 'Most violated assets') if top_names else None

    # Impact calculations
    revenue_protected = total_violations * 3000   # ₹3,000 per violation estimate
    hours_protected = total_assets * 3              # 3 hours broadcast per asset estimate

    return {
        'total_assets': total_assets,
        'total_violations': total_violations,
        'scans_today': scans_today,
        'critical_count': critical_count,
        'dmca_count': dmca_count,
        'revenue_protected': revenue_protected,
        'hours_protected': hours_protected,
        'chart1': chart1,
        'chart2': chart2
    }
