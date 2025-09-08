from flask import Flask, request, jsonify
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import os
import base64
import json

app = Flask(__name__)

# Папка для сохранения вложений
ATTACHMENTS_DIR = "attachments"
if not os.path.exists(ATTACHMENTS_DIR):
    os.makedirs(ATTACHMENTS_DIR)

def clean_filename(filename):
    """Очистка имени файла для безопасного сохранения."""
    return "".join(c for c in filename if c.isalnum() or c in ('.', '_')).rstrip()

@app.route('/get_emails', methods=['POST'])
def get_emails():
    try:
        # Получаем данные из запроса
        data = request.get_json()
        email_user = data.get('email')
        email_pass = data.get('password')
        if not email_user or not email_pass:
            return jsonify({"error": "Email and password are required"}), 400

        # Подключение к IMAP-серверу
        mail = imaplib.IMAP4_SSL("imap.mail.ru")
        mail.login(email_user, email_pass)
        mail.select("inbox")

        # Дата 3 месяца назад
        since_date = (datetime.now() - timedelta(days=90)).strftime("%d-%b-%Y")
        status, data = mail.search(None, f'(SINCE "{since_date}")')
        email_ids = data[0].split()
        emails = []

        for eid in email_ids:
            status, msg_data = mail.fetch(eid, "(RFC822)")
            raw_msg = msg_data[0][1]
            msg = email.message_from_bytes(raw_msg)

            # Декодируем тему
            subject, encoding = decode_header(msg["Subject"])[0] if msg["Subject"] else ("No Subject", None)
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or 'utf-8')

            # Декодируем отправителя
            from_, encoding = decode_header(msg["From"])[0] if msg["From"] else ("No Sender", None)
            if isinstance(from_, bytes):
                from_ = from_.decode(encoding or 'utf-8')

            # Получаем тело письма (text/plain или text/html)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break
                    elif part.get_content_type() == "text/html":
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
            else:
                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

            # Получаем вложения
            attachments = []
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    if part.get('Content-Disposition') is None:
                        continue
                    filename = part.get_filename()
                    if filename:
                        filename, encoding = decode_header(filename)[0]
                        if isinstance(filename, bytes):
                            filename = filename.decode(encoding or 'utf-8')
                        filename = clean_filename(filename)
                        
                        # Создаем уникальное имя файла с использованием времени и ID письма
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        unique_filename = f"{timestamp}_{eid.decode()}_{filename}"
                        filepath = os.path.join(ATTACHMENTS_DIR, unique_filename)
                        
                        # Сохраняем файл на диск
                        with open(filepath, 'wb') as f:
                            f.write(part.get_payload(decode=True))
                        
                        # Читаем файл для кодирования в base64
                        with open(filepath, 'rb') as f:
                            attachment_data = base64.b64encode(f.read()).decode('utf-8')
                        
                        attachments.append({
                            "filename": filename,
                            "content_type": part.get_content_type(),
                            "data": attachment_data,
                            "saved_path": filepath
                        })

            emails.append({
                "from": from_,
                "subject": subject,
                "date": msg["Date"],
                "body": body,
                "attachments": attachments
            })

        mail.logout()
        return jsonify({"emails": emails, "count": len(emails)}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
