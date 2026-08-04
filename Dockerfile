FROM python:3.10-slim

# تثبيت أداة FFmpeg لمعالجة الفيديوهات برمجياً
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نسخ ملف متطلبات المكتبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ كود التطبيق
COPY . .

# تشغيل خادم المخرج على المنفذ المحدد من Cloud Run
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} 
