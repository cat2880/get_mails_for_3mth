from flask import Flask, request, jsonify, Response
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import os
import io
from werkzeug.datastructures import MultiDict
from werkzeug.formparser import parse_form_data

app = Flask(__name__)

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
                        # Получаем бинарные данные вложения
                        attachment_data = part.get_payload(decode=True)
                        attachments.append({
                            "filename": filename,
                            "content_type": part.get_content_type(),
                            "data": attachment_data  # Сохраняем бинарные данные
                        })

            emails.append({
                "from": from_,
                "subject": subject,
                "date": msg["Date"],
                "body": body,
                "attachment_count": len(attachments)
            })

            # Если есть вложения, добавляем их как multipart/form-data
            if attachments:
                # Создаем multipart/form-data ответ
                form_data = MultiDict()
                form_data.add('emails', json.dumps({"emails": emails, "count": len(emails)}))
                
                files = {
                    f"attachment_{i}": (attachment["filename"], io.BytesIO(attachment["data"]), attachment["content_type"])
                    for i, attachment in enumerate(attachments)
                }
                
                # Парсим данные для создания правильного ответа
                environ = {'REQUEST_METHOD': 'POST'}
                stream, form, files = parse_form_data(environ, stream=None, form=form_data, files=files)
                
                return Response(
                    stream,
                    content_type=form.content_type,
                    status=200
                )

        mail.logout()
        # Если нет вложений, возвращаем JSON
        return jsonify({"emails": emails, "count": len(emails)}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/<filename>', methods=['GET'])
def download_file(filename):
    attachments_dir = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', 'attachments')
    file_path = os.path.join(attachments_dir, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)  # Автоматически определяет Content-Type по расширению
    return jsonify({"error": "File not found"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
