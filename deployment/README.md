Step-by-step deployment commands:
1. Copy nginx config, enable site, get certbot cert
2. Copy systemd service, enable, start
3. Install cron file
4. Verify /health endpoint
5. Send test SMS to yourself
6. Verify status callback updates DB
7. Reply STOP to test number and verify opt-out row created
8. Reply YES to test number and verify reply_processor classifies it
