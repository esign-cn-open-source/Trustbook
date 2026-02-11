FROM python:3.11-slim
ADD . /minibook
RUN pip install --no-cache-dir -r /minibook/requirements.txt
CMD ["sh", "-c", "python run.py"]
