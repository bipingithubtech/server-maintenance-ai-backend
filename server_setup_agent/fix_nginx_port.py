import paramiko, base64

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.56.101', username='bipin123', password='bipin', port=22)

# Check what is actually in sites-available
_, out, _ = client.exec_command("cat -A /etc/nginx/sites-available/ats-frontend.conf")
print("=== CURRENT FILE ===")
print(out.read().decode())

good_config = (
    "server {\n"
    "    listen 80;\n"
    "    server_name 192.168.56.101;\n"
    "    access_log /var/log/nginx/ats-frontend.access.log;\n"
    "    error_log  /var/log/nginx/ats-frontend.error.log warn;\n"
    "    gzip on;\n"
    "    gzip_types text/plain text/css application/json application/javascript;\n"
    "    client_max_body_size 100M;\n"
    "    location / {\n"
    "        proxy_pass         http://127.0.0.1:5006;\n"
    "        proxy_http_version 1.1;\n"
    "        proxy_set_header   Host $host;\n"
    "        proxy_set_header   X-Real-IP $remote_addr;\n"
    "        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;\n"
    "        proxy_read_timeout 3600s;\n"
    "    }\n"
    "    location ~ /\\. { deny all; }\n"
    "}\n"
)

encoded = base64.b64encode(good_config.encode()).decode()
cmd = f"echo '{encoded}' | base64 --decode | sudo tee /etc/nginx/sites-available/ats-frontend.conf > /dev/null"
_, out, err = client.exec_command(cmd)
out.read(); err.read()

# Re-link and test
for cmd in [
    "sudo rm -f /etc/nginx/sites-enabled/ats-frontend.conf",
    "sudo ln -sf /etc/nginx/sites-available/ats-frontend.conf /etc/nginx/sites-enabled/ats-frontend.conf",
]:
    _, out, err = client.exec_command(cmd)
    out.read(); err.read()

_, out, err = client.exec_command("sudo nginx -t 2>&1 && sudo systemctl reload nginx 2>&1 && echo NGINX_OK")
result = out.read().decode() + err.read().decode()
print("\n=== NGINX RESULT ===")
print(result)

client.close()
