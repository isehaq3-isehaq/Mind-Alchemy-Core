import os
import json
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from google.cloud import storage
import google.generativeai as genai
import requests
from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip, VideoFileClip

app = FastAPI(title="AI Educational Video Engine")

# ---------------------------------------------------------
# 1. تهيئة المتغيرات ومفاتيح الـ APIs
# ---------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")

genai.configure(api_key=GEMINI_API_KEY)
storage_client = storage.Client()

# معرفات أصوات الشخصيات في ElevenLabs (يتم استبدالها بـ Voice IDs الخاصة بك)
VOICE_IDS = {
    "تعابير": "VOICE_ID_TAABEER",
    "رسّام": "VOICE_ID_RASSAM"
}

class LessonRequest(BaseModel):
    topic: str
    duration_seconds: int = 40
    background_image_name: str

# ---------------------------------------------------------
# 2. وظيفة توليد النص الحواري عبر Gemini API
# ---------------------------------------------------------
def generate_script(topic: str, duration: int):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    قم بكتابة سيناريو تعليمي للأطفال باللغة العربية حول موضوع: "{topic}".
    المدة المطلوبة: {duration} ثانية.
    الشخصيات المتاحة: "تعابير" و "رسّام".
    
    قم بإرجاع النتيجة بصيغة JSON حصرية بالطريقة التالية دون أي نص إضافي:
    [
        {{"character": "تعابير", "text": "النص هنا", "duration": 5}},
        {{"character": "رسّام", "text": "النص هنا", "duration": 6}}
    ]
    """
    response = model.generate_content(prompt)
    clean_json = response.text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean_json)

# ---------------------------------------------------------
# 3. وظيفة تحويل النص إلى صوت عبر ElevenLabs API
# ---------------------------------------------------------
def text_to_speech(character: str, text: str, output_path: str):
    voice_id = VOICE_IDS.get(character)
    if not voice_id:
        raise ValueError(f"شخصية غير معروفة: {character}")
        
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY
    }
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }
    response = requests.post(url, json=data, headers=headers)
    if response.status_code == 200:
        with open(output_path, "wb") as f:
            f.write(response.content)
    else:
        raise Exception(f"خطأ في توليد الصوت: {response.text}")

# ---------------------------------------------------------
# 4. وظيفة تنزيل الملفات من Google Cloud Storage
# ---------------------------------------------------------
def download_from_gcs(blob_name: str, local_path: str):
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(local_path)

# ---------------------------------------------------------
# 5. محرك دمج الطبقات وإخراج الفيديو النهائي (FFmpeg / MoviePy)
# ---------------------------------------------------------
def render_video(script_data, bg_image_name: str, output_video_path: str):
    # 1. تنزيل صورة الخلفية
    local_bg_path = f"/tmp/{bg_image_name}"
    download_from_gcs(f"backgrounds/{bg_image_name}", local_bg_path)
    
    # 2. تنزيل أصول الشخصيات (صور مفرغة PNG)
    local_taabeer_path = "/tmp/taabeer.png"
    local_rassam_path = "/tmp/rassam.png"
    download_from_gcs("characters/taabeer.png", local_taabeer_path)
    download_from_gcs("characters/rassam.png", local_rassam_path)

    # 3. حساب الطول الإجمالي للفيديو
    total_duration = sum([item['duration'] for item in script_data])
    
    # تجهيز طبقة الخلفية
    bg_clip = ImageClip(local_bg_path).set_duration(total_duration).resize(newsize=(1080, 1920))
    
    # تجهيز كليبات الشخصيات بمواقع ثابتة (يمين ويسار الشاشة)
    taabeer_clip = ImageClip(local_taabeer_path).set_duration(total_duration).resize(width=450).set_position((50, 900))
    rassam_clip = ImageClip(local_rassam_path).set_duration(total_duration).resize(width=450).set_position((580, 900))

    # 4. توليد الأصوات ودمج التزامن الزمني
    audio_clips = []
    current_time = 0
    
    for idx, line in enumerate(script_data):
        audio_file = f"/tmp/speech_{idx}.mp3"
        text_to_speech(line['character'], line['text'], audio_file)
        
        audio = AudioFileClip(audio_file).set_start(current_time)
        audio_clips.append(audio)
        current_time += line['duration']

    # 5. دمج كل الطبقات
    final_video = CompositeVideoClip([bg_clip, taabeer_clip, rassam_clip])
    final_audio = CompositeAudioClip(audio_clips) if 'CompositeAudioClip' in globals() else audio_clips[0] # التجميع الصوتي
    final_video = final_video.set_audio(final_audio)

    # 6. التصدير النهائي بصيغة MP4 موحدة لـ Shorts
    final_video.write_videofile(
        output_video_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        preset="ultrafast"
    )

# ---------------------------------------------------------
# 6. نقطة الاتصال (API Endpoint) لإنشاء الفيديو
# ---------------------------------------------------------
@app.post("/generate-lesson-video")
async def create_lesson_video(request: LessonRequest, background_tasks: BackgroundTasks):
    try:
        # توليد الحوار تلقائياً
        script = generate_script(request.topic, request.duration_seconds)
        
        output_path = f"/tmp/final_{request.topic.replace(' ', '_')}.mp4"
        
        # تشغيل التجميع والمعالجة
        render_video(script, request.background_image_name, output_path)
        
        # رفع الفيديو الناتج إلى Storage Bucket
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(f"exports/final_{request.topic}.mp4")
        blob.upload_from_filename(output_path)
        
        return {
            "status": "success",
            "message": "تم إنتاج الفيديو بنجاح",
            "download_url": blob.public_url,
            "script_used": script
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
