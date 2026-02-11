#!/bin/bash
# Trustbook startup script - uses tmux for persistence

cd /home/pi/trustbook

# Kill existing sessions
tmux kill-session -t trustbook-be 2>/dev/null
tmux kill-session -t trustbook-fe 2>/dev/null

# Start backend
tmux new-session -d -s trustbook-be -c /home/pi/trustbook \
  "source venv/bin/activate && uvicorn src.main:app --host 0.0.0.0 --port 3456 2>&1 | tee /tmp/trustbook-backend.log"

# Wait for backend
sleep 2

# Start frontend  
tmux new-session -d -s trustbook-fe -c /home/pi/trustbook/frontend \
  "PORT=3457 npm start 2>&1 | tee /tmp/trustbook-frontend.log"

sleep 3
echo "Status:"
tmux ls
curl -s http://localhost:3456/health && echo " <- backend OK"
curl -s http://localhost:3457/api/v1/site-config && echo " <- frontend OK"
