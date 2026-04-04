from flask import Blueprint, current_app, make_response, request, jsonify
from database.db import get_db
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.units import cm
import io
import os
import smtplib
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

report_bp = Blueprint('report', __name__)


# ============================================================
# HELPER — Build DMCA PDF bytes (shared by download + email)
# ============================================================

def build_dmca_pdf(violation):
    from routes.blockchain import load_chain
    chain = load_chain()
    evidence_hash = "N/A"
    for block in chain:
        if (block.get('type') == 'VIOLATION' and
                block['data'].get('asset_name') == violation['asset_name']):
            evidence_hash = block['data'].get('evidence_hash', 'N/A')
            break

    sim = violation['similarity']
    risk = 'CRITICAL' if sim >= 90 else 'HIGH' if sim >= 70 else 'MEDIUM'
    detected_date = violation['detected_at'][:10] if violation['detected_at'] else datetime.now().strftime('%Y-%m-%d')
    found_url = violation['found_url'] or "[URL of infringing content]"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=2.5*cm, leftMargin=2.5*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    elements = []

    org_style = ParagraphStyle('org', fontSize=20, fontName='Helvetica-Bold',
        textColor=colors.HexColor('#4f46e5'), spaceAfter=2)
    tagline_style = ParagraphStyle('tag', fontSize=9, fontName='Helvetica',
        textColor=colors.HexColor('#64748b'), spaceAfter=4)
    heading_style = ParagraphStyle('heading', fontSize=14, fontName='Helvetica-Bold',
        textColor=colors.HexColor('#0f172a'), spaceAfter=12, spaceBefore=16, alignment=1)
    body_style = ParagraphStyle('body', fontSize=10, fontName='Helvetica',
        textColor=colors.HexColor('#1e293b'), spaceAfter=10, leading=16)
    bold_style = ParagraphStyle('bold', fontSize=10, fontName='Helvetica-Bold',
        textColor=colors.HexColor('#0f172a'), spaceAfter=6)
    footer_style = ParagraphStyle('footer', fontSize=8, fontName='Helvetica',
        textColor=colors.HexColor('#94a3b8'), alignment=1)

    elements.append(Paragraph('SportShield AI', org_style))
    elements.append(Paragraph('Digital Asset Protection System', tagline_style))
    elements.append(HRFlowable(width="100%", thickness=1,
                               color=colors.HexColor('#e2e8f0'), spaceAfter=16))
    elements.append(Paragraph(f'Date: {datetime.now().strftime("%d %B %Y")}',
        ParagraphStyle('date', fontSize=10, fontName='Helvetica',
                       textColor=colors.HexColor('#475569'), spaceAfter=4)))
    elements.append(Paragraph(
        f'Reference No: DMCA-{violation["id"]:05d}-{datetime.now().strftime("%Y%m%d")}',
        ParagraphStyle('ref', fontSize=10, fontName='Helvetica',
                       textColor=colors.HexColor('#475569'), spaceAfter=20)))

    elements.append(Paragraph('DMCA TAKEDOWN NOTICE', heading_style))
    elements.append(Paragraph(
        'Pursuant to 17 U.S.C. § 512(c)(3) of the Digital Millennium Copyright Act',
        ParagraphStyle('sub_heading', fontSize=9, fontName='Helvetica',
                       textColor=colors.HexColor('#64748b'), alignment=1, spaceAfter=20)))

    elements.append(Paragraph('To Whom It May Concern,', body_style))
    elements.append(Paragraph(
        'I am an authorized representative of <b>SportShield AI</b>, '
        'the lawful owner of the intellectual property described below. '
        'I write to notify you of infringing activity and request immediate removal.',
        body_style))

    elements.append(Paragraph('1. Identification of Copyrighted Work', bold_style))
    work_data = [
        ['Asset Name', violation['asset_name']],
        ['Owner / Rights Holder', 'SportShield AI'],
        ['Protection Method', 'AI Fingerprinting + Blockchain Registration'],
    ]
    wt = Table(work_data, colWidths=[6*cm, 10*cm])
    wt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f1f5f9')),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('PADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(wt)
    elements.append(Spacer(1, 0.4*cm))

    elements.append(Paragraph('2. Identification of Infringing Material', bold_style))
    infringe_data = [
        ['Infringing URL', found_url],
        ['Detection Date', detected_date],
        ['Similarity Score', f'{sim}% match'],
        ['Risk Level', risk],
        ['Detection Methods', 'pHash, dHash, aHash, SIFT/ORB, MobileNet AI'],
        ['Blockchain Evidence Hash', evidence_hash],
    ]
    it = Table(infringe_data, colWidths=[6*cm, 10*cm])
    it.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#fff1f2')),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#fecdd3')),
        ('PADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(it)
    elements.append(Spacer(1, 0.4*cm))

    elements.append(Paragraph('3. Good Faith Statement', bold_style))
    elements.append(Paragraph(
        'I have a good faith belief that the use of the described material is not authorized '
        'by the copyright owner, its agent, or the law.', body_style))

    elements.append(Paragraph('4. Statement of Accuracy', bold_style))
    elements.append(Paragraph(
        'The information in this notification is accurate, and under penalty of perjury, '
        'I am authorized to act on behalf of the owner of the exclusive right allegedly infringed.',
        body_style))

    elements.append(Paragraph('5. Requested Action', bold_style))
    elements.append(Paragraph(
        'Please immediately: (a) remove or disable access to the infringing material, '
        '(b) notify the infringing party, and (c) confirm removal to us.', body_style))

    elements.append(Spacer(1, 0.4*cm))
    elements.append(HRFlowable(width="100%", thickness=0.5,
                               color=colors.HexColor('#e2e8f0'), spaceAfter=12))
    elements.append(Paragraph('Sincerely,', body_style))
    elements.append(Paragraph(
        f'<b>SportShield AI</b><br/>Digital Asset Protection System<br/>'
        f'Generated: {datetime.now().strftime("%d %B %Y, %H:%M")}<br/>'
        'Blockchain-verified evidence available upon request.',
        ParagraphStyle('sig', fontSize=10, fontName='Helvetica',
                       textColor=colors.HexColor('#1e293b'), leading=18, spaceAfter=20)))
    elements.append(HRFlowable(width="100%", thickness=0.5,
                               color=colors.HexColor('#e2e8f0'), spaceAfter=8))
    elements.append(Paragraph(
        f'Auto-generated by SportShield AI · Violation ID: {violation["id"]} · '
        f'Evidence Hash: {evidence_hash}', footer_style))

    doc.build(elements)
    buf.seek(0)
    return buf.read(), violation['asset_name'], evidence_hash


# ============================================================
# EXISTING — Violation Summary PDF Report
# ============================================================

@report_bp.route('/report/export')
def export_pdf():
    db = get_db(current_app.config['DATABASE'])
    total_assets = db.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
    total_violations = db.execute('SELECT COUNT(*) FROM violations').fetchone()[0]
    violations = db.execute('''
        SELECT v.similarity, v.detected_at, a.name as asset_name
        FROM violations v JOIN assets a ON v.asset_id = a.id
        ORDER BY v.detected_at DESC
    ''').fetchall()
    db.close()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    elements = []

    title_style = ParagraphStyle('title', fontSize=22, fontName='Helvetica-Bold',
        textColor=colors.HexColor('#4f46e5'), spaceAfter=6)
    sub_style = ParagraphStyle('sub', fontSize=10, fontName='Helvetica',
        textColor=colors.HexColor('#475569'), spaceAfter=20)

    elements.append(Paragraph('SportShield AI', title_style))
    elements.append(Paragraph(
        f'Violation Report — Generated {datetime.now().strftime("%d %b %Y, %H:%M")}', sub_style))

    summary_data = [
        ['Metric', 'Value'],
        ['Total Registered Assets', str(total_assets)],
        ['Total Violations Detected', str(total_violations)],
        ['Report Generated', datetime.now().strftime('%d %b %Y %H:%M')],
    ]
    st = Table(summary_data, colWidths=[10*cm, 6*cm])
    st.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4f46e5')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f8fafc'), colors.white]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('PADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(st)
    elements.append(Spacer(1, 0.5*cm))

    if violations:
        elements.append(Paragraph('Violation Log', ParagraphStyle('h2',
            fontSize=14, fontName='Helvetica-Bold',
            textColor=colors.HexColor('#0f172a'), spaceAfter=10, spaceBefore=10)))
        vdata = [['Asset Name', 'Similarity', 'Risk Level', 'Detected At']]
        for v in violations:
            sim = v['similarity']
            risk = 'CRITICAL' if sim >= 90 else 'HIGH' if sim >= 70 else 'MEDIUM' if sim >= 50 else 'LOW'
            vdata.append([v['asset_name'], f"{sim}%", risk, v['detected_at'][:16]])
        vt = Table(vdata, colWidths=[6*cm, 3*cm, 3*cm, 5*cm])
        vt.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e11d48')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#fff1f2'), colors.white]),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
            ('PADDING', (0,0), (-1,-1), 7),
        ]))
        elements.append(vt)

    elements.append(Spacer(1, 1*cm))
    elements.append(Paragraph(
        'Auto-generated by SportShield AI — Digital Asset Protection System.',
        ParagraphStyle('footer', fontSize=8, textColor=colors.HexColor('#94a3b8'))))

    doc.build(elements)
    buf.seek(0)

    response = make_response(buf.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = \
        f'attachment; filename=sportshield_report_{datetime.now().strftime("%Y%m%d")}.pdf'
    return response


# ============================================================
# DMCA — Download PDF
# ============================================================

@report_bp.route('/report/dmca/<int:violation_id>')
def generate_dmca(violation_id):
    db = get_db(current_app.config['DATABASE'])
    violation = db.execute('''
        SELECT v.id, v.similarity, v.detected_at, v.found_url,
               a.name as asset_name, a.filename as asset_filename
        FROM violations v JOIN assets a ON v.asset_id = a.id
        WHERE v.id = ?
    ''', (violation_id,)).fetchone()
    db.close()

    if not violation:
        return "Violation not found", 404

    pdf_bytes, asset_name, _ = build_dmca_pdf(dict(violation))

    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = \
        f'attachment; filename=DMCA_{asset_name.replace(" ", "_")}_{datetime.now().strftime("%Y%m%d")}.pdf'
    return response


# ============================================================
# DMCA — Send via Email directly from app
# ============================================================

@report_bp.route('/report/dmca/<int:violation_id>/email', methods=['POST'])
def email_dmca(violation_id):
    """
    Send DMCA notice directly to recipient email from app.
    POST JSON: { "recipient_email": "abuse@website.com" }
    """
    data = request.get_json() or {}
    recipient = data.get('recipient_email', '').strip()

    if not recipient or '@' not in recipient:
        return jsonify({'success': False, 'error': 'Invalid email address'}), 400

    db = get_db(current_app.config['DATABASE'])
    violation = db.execute('''
        SELECT v.id, v.similarity, v.detected_at, v.found_url,
               a.name as asset_name, a.filename as asset_filename
        FROM violations v JOIN assets a ON v.asset_id = a.id
        WHERE v.id = ?
    ''', (violation_id,)).fetchone()
    db.close()

    if not violation:
        return jsonify({'success': False, 'error': 'Violation not found'}), 404

    sender = os.getenv('MAIL_EMAIL')
    password = os.getenv('MAIL_PASSWORD')

    if not sender or not password:
        return jsonify({'success': False,
                        'error': 'Email not configured. Add MAIL_EMAIL and MAIL_PASSWORD to .env'}), 500

    try:
        pdf_bytes, asset_name, evidence_hash = build_dmca_pdf(dict(violation))

        msg = MIMEMultipart()
        msg['Subject'] = f'DMCA Takedown Notice — {asset_name} (Ref: DMCA-{violation_id:05d})'
        msg['From'] = sender
        msg['To'] = recipient

        body = f"""Dear Sir/Madam,

Please find attached a formal DMCA Takedown Notice regarding unauthorized use of
protected sports media asset: {asset_name}

Similarity Score: {violation['similarity']}%
Blockchain Evidence Hash: {evidence_hash}
Detection Date: {violation['detected_at'][:10] if violation['detected_at'] else 'N/A'}

We request immediate removal of the infringing content.

This notice was generated by SportShield AI — Digital Asset Protection System.
"""
        msg.attach(MIMEText(body, 'plain'))

        # Attach PDF
        part = MIMEBase('application', 'pdf')
        part.set_payload(pdf_bytes)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition',
                        f'attachment; filename=DMCA_{asset_name.replace(" ", "_")}.pdf')
        msg.attach(part)

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())

        return jsonify({
            'success': True,
            'message': f'DMCA notice sent to {recipient}'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
