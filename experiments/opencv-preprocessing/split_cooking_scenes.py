"""
요리 영상 씬 분할 스크립트
- threshold=27.0 (SampleVideo_Scenes와 동일 기준)
- 출력: SampleVideo_Scenes2/
"""
import os
import glob
import sys

sys.stdout.reconfigure(encoding='utf-8')

from scenedetect import detect, ContentDetector
from scenedetect.video_splitter import split_video_ffmpeg

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "SampleVideo_Scenes2")

# sample1.mp4 (요리 영상 클립) 찾기
matches = glob.glob(os.path.join(BASE_DIR, "*sample1.mp4"))
if not matches:
    print("ERROR: *sample1.mp4 파일을 찾을 수 없습니다.")
    sys.exit(1)

video_path = matches[0]
print(f"영상 파일: {os.path.basename(video_path)}")

os.makedirs(OUTPUT_DIR, exist_ok=True)

print("씬 감지 중... (threshold=27.0)")
scene_list = detect(video_path, ContentDetector(threshold=27.0))

if not scene_list:
    print("감지된 씬이 없습니다.")
    sys.exit(1)

print(f"총 {len(scene_list)}개 씬 감지됨. 분할 저장 시작...")

original_cwd = os.getcwd()
os.chdir(OUTPUT_DIR)

split_video_ffmpeg(
    video_path,
    scene_list,
    output_file_template="SampleVideo2-Scene-$SCENE_NUMBER.mp4",
    show_progress=True
)

os.chdir(original_cwd)

saved = len(glob.glob(os.path.join(OUTPUT_DIR, "*.mp4")))
print(f"\n씬 분할 완료! 저장 파일 수: {saved}개 → {OUTPUT_DIR}")
