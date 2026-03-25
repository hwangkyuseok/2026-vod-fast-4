import os
import glob
import subprocess
try:
    from faster_whisper import WhisperModel
except ImportError:
    print("faster_whisper가 설치되지 않았습니다. 모델을 불러올 수 없습니다.")
    print("명령어 예시: pip install faster-whisper")

def get_video_duration(filename):
    """ffprobe를 이용해 비디오 길이를 가져옵니다."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", 
         "-of", "default=noprint_wrappers=1:nokey=1", filename],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    return float(result.stdout.strip())

def main():
    video_path = r"data\videos\언더커버 미쓰홍.E16.260308.720p-NEXT.mp4"
    reference_txt = r"data\references\undercover_miss_hong.txt"
    scene_clips_folder = r"scene_clips_result"
    output_merged_folder = r"outputs\dialogue_aligned_clips"
    
    os.makedirs(output_merged_folder, exist_ok=True)

    print("=== 1. Faster-Whisper로 대사 추출 및 정답지 생성 ===")
    
    try:
        # GPU 사용 권장 (만약 CUDA 에러가 난다면 device="cpu", compute_type="int8"로 변경하세요)
        model = WhisperModel("large-v3", device="cuda", compute_type="float16")
        print("Whisper 모델 로드 완료. 대사 추출을 시작합니다... (영상이 길어 시간이 다소 소요될 수 있습니다)")
        
        segments, info = model.transcribe(video_path, beam_size=5, language="ko")
        
        dialogues = []
        full_text = []

        for segment in segments:
            print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")
            dialogues.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip()
            })
            full_text.append(segment.text.strip())

        # 정답지(undercover_miss_hong.txt)에 내용 덮어쓰기
        with open(reference_txt, "w", encoding="utf-8") as f:
            f.write(" ".join(full_text))
        print(f"정답지 업데이트 완료: {reference_txt}")

    except Exception as e:
        print(f"Whisper 대사 추출 중 예외가 발생하여 건너뜁니다: {e}")
        return

    print("\n=== 2. 원본 Scene 클립들의 타임라인 분석 ===")
    scene_files = sorted(glob.glob(os.path.join(scene_clips_folder, "*.mp4")))
    if not scene_files:
        print(f"{scene_clips_folder} 폴더에 mp4 파일이 없습니다.")
        return
        
    scene_timelines = []
    current_time = 0.0
    for sf in scene_files:
        try:
            dur = get_video_duration(sf)
            scene_timelines.append({
                "file": sf,
                "start": current_time,
                "end": current_time + dur
            })
            current_time += dur
        except Exception as e:
            print(f"[{sf}] 길이 측정 실패: {e}")

    print("\n=== 3. 대사(문맥) 기준 타임테이블에 맞춰 Scene 병합 ===")
    # dialogues에 있는 대사 타임아웃에 겹치는 scene들을 찾아서 병합
    for idx, d in enumerate(dialogues):
        d_start = d["start"]
        d_end = d["end"]
        d_text = d["text"]
        
        # 문맥 길이에 겹치는 scene들 필터링
        intersect_scenes = []
        for st in scene_timelines:
            # 시간 겹침 조건: 씬의 끝이 대사 시작보다 크고, 씬의 시작이 대사 끝보다 작을 때
            if st["end"] > d_start and st["start"] < d_end:
                intersect_scenes.append(st["file"])
                
        if not intersect_scenes:
            continue
            
        print(f"대사 {idx+1}: [{d_start:.1f}~{d_end:.1f}] '{d_text}' -> 파일 {len(intersect_scenes)}개 병합")
        
        # ffmpeg concat을 위한 텍스트 리스트 파일 생성
        list_file_path = os.path.join(output_merged_folder, f"list_{idx}.txt")
        with open(list_file_path, "w", encoding="utf-8") as lf:
            for sfp in intersect_scenes:
                # Windows 환경 고려 절대/상대 경로 이스케이프
                lf.write(f"file '../../{sfp}'\n".replace("\\", "/"))
                
        out_filename = os.path.join(output_merged_folder, f"dialogue_{idx+1:04d}.mp4")
        
        # ffmpeg로 재인코딩 없이(copy) 병합
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
            "-i", list_file_path, 
            "-c", "copy", out_filename
        ]
        
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 리스트 파일 정리
        if os.path.exists(list_file_path):
            os.remove(list_file_path)

    print("\n모든 작업이 완료되었습니다! 결과 폴더:", output_merged_folder)

if __name__ == "__main__":
    main()
