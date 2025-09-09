from flask import Flask, request, jsonify, send_file, abort
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import os
import base64

app = Flask(__name__)

def clean_filename(filename):
    """Очистка имени файла для безопасного сохранения."""
    return "".join(c for c in filename if c.isalnum() or c in ('.', '_')).rstrip()

# Папка для сохранения вложений (используем volume на Railway)
ATTACHMENTS_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', 'attachments')
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

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

            # Получаем тело письма
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

            # Получаем имена вложений (без бинарных данных)
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
                        # Сохраняем файл на диск
                        attachment_data = part.get_payload(decode=True)
                        file_path = os.path.join(ATTACHMENTS_DIR, filename)
                        with open(file_path, 'wb') as f:
                            f.write(attachment_data)
                            app.logger.info(f"Saved file: {file_path}")  # Логирование сохранения файла
                        attachments.append({
                            "filename": filename,
                            "content_type": part.get_content_type()
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
        app.logger.error(f"Error in get_emails: {str(e)}")  # Логирование ошибок
        return jsonify({"error": str(e)}), 500

@app.route('/download', methods=['POST'])
def download_file():
    """Эндпоинт для скачивания файлов по имени, переданному в теле запроса."""
    try:
        data = request.get_json()
        filename = data.get('filename')
        if not filename:
            return jsonify({"error": "Filename is required"}), 400

        file_path = os.path.join(ATTACHMENTS_DIR, filename)
        app.logger.info(f"Requested file: {file_path}, exists: {os.path.exists(file_path)}")  # Логирование запроса
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True)
        return jsonify({"error": "File not found"}), 404

    except Exception as e:
        app.logger.error(f"Error in download_file: {str(e)}")  # Логирование ошибок
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
