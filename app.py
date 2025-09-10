from flask import Flask, request, jsonify, send_file, abort
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import os
import base64
import json

app = Flask(__name__)

def clean_filename(filename):
    """Очистка имени файла для безопасного сохранения."""
    return "".join(c for c in filename if c.isalnum() or c in ('.', '_')).rstrip()

# Папка для сохранения вложений (используем volume на Railway)
ATTACHMENTS_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', 'attachments')
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

# Файл для хранения последнего UID
STATE_FILE = 'last_sent_uid.json'

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

            # Декодируем получателя
            to_, encoding = decode_header(msg["To"])[0] if msg["To"] else ("No Recipient", None)
            if isinstance(to_, bytes):
                to_ = to_.decode(encoding or 'utf-8')

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

            # Получаем имена и содержимое вложений
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
                        # Добавляем base64-кодированные данные
                        with open(file_path, 'rb') as f:
                            base64_data = base64.b64encode(f.read()).decode('utf-8')
                        attachments.append({
                            "filename": filename,
                            "content_type": part.get_content_type(),
                            "data": base64_data
                        })

            emails.append({
                "from": from_,
                "to": to_,
                "subject": subject,
                "date": msg["Date"],
                "body": body,
                "attachments": attachments
            })

        mail.logout()
        return jsonify({"emails": emails, "count": len(emails)}), 200

    except Exception as e:
        app.logger.error(f"Error in get_emails: {str(e)}")
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
        app.logger.info(f"Requested file: {file_path}, exists: {os.path.exists(file_path)}")
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True)
        return jsonify({"error": "File not found"}), 404

    except Exception as e:
        app.logger.error(f"Error in download_file: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/check_sent', methods=['POST'])
def check_sent():
    """Проверка новых отправленных писем в папке 'Отправленные'."""
    try:
        # Получаем данные из запроса (email и пароль из формы)
        data = request.get_json()
        email_user = data.get('email')
        email_pass = data.get('password')
        if not email_user or not email_pass:
            return jsonify({"error": "Email and password are required"}), 400

        # Загружаем состояние (последний UID)
        last_uid = 0
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                last_uid = state.get('last_uid', 0)

        new_emails = []
        max_uid = last_uid

        # Подключаемся к IMAP
        with imaplib.IMAP4_SSL("imap.mail.ru") as mail:
            mail.login(email_user, email_pass)
            mail.select("Отправленные", readonly=True)

            # Ищем все сообщения с UID > last_uid
            status, messages = mail.uid('search', None, f'(UID {last_uid + 1}:*)')
            if status == 'OK' and messages[0]:
                uids = messages[0].split()
                for uid in uids:
                    uid_num = int(uid)
                    if uid_num > max_uid:
                        max_uid = uid_num

                    # Fetch email
                    status, msg_data = mail.uid('fetch', uid, '(RFC822)')
                    if status == 'OK':
                        raw_email = msg_data[0][1]
                        msg = email.message_from_bytes(raw_email)

                        # Декодируем данные
                        subject = decode_str(msg['Subject']) if msg['Subject'] else ''
                        from_ = decode_str(msg['From']) if msg['From'] else ''
                        to_ = decode_str(msg['To']) if msg['To'] else ''
                        date = msg['Date']
                        body = ''
                        attachments = []

                        # Парсим тело и вложения
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == 'text/plain':
                                    body = part.get_payload(decode=True).decode(errors='ignore')
                                elif part.get_filename():
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
                                        # Добавляем base64-кодированные данные
                                        with open(file_path, 'rb') as f:
                                            base64_data = base64.b64encode(f.read()).decode('utf-8')
                                        attachments.append({
                                            "filename": filename,
                                            "content_type": part.get_content_type(),
                                            "data": base64_data
                                        })
                        else:
                            body = msg.get_payload(decode=True).decode(errors='ignore')

                        new_emails.append({
                            'uid': uid_num,
                            'subject': subject,
                            'from': from_,
                            'to': to_,
                            'date': date,
                            'body': body,
                            'attachments': attachments
                        })

        # Сохраняем новый max_uid
        with open(STATE_FILE, 'w') as f:
            json.dump({'last_uid': max_uid}, f)

        return jsonify({'new_emails': new_emails, 'count': len(new_emails)}), 200

    except Exception as e:
        app.logger.error(f"Error in check_sent: {str(e)}")
        return jsonify({"error": str(e)}), 500

def decode_str(s):
    """Декодирование заголовков."""
    decoded = decode_header(s)[0]
    if isinstance(decoded[0], bytes):
        return decoded[0].decode(decoded[1] or 'utf-8')
    return decoded[0]

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
