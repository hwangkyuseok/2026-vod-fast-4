import os
import argparse
import subprocess
import imageio_ffmpeg
import numpy as np
import csv

try:
    from faster_whisper import WhisperModel
    from sentence_transformers import SentenceTransformer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    print("관련 환경 모듈이 모두 설치되지 않았습니다. (.myvenv 파이썬 환경을 사용하세요)")
    exit(1)

def chunk_video_by_context(video_path: str, output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    basename = os.path.splitext(os.path.basename(video_path))[0]
    
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    temp_wav = os.path.join(output_dir, f"{basename}_temp.wav")

    print(f"\n[1/4] 비디오에서 오디오 안전 분리 중... ({video_path})")
    # 분할 시 Whisper가 다운되지 않도록 16kHz WAV 포맷으로 백그라운드 추출
    if not os.path.exists(temp_wav):
        subprocess.run([
            ffmpeg_exe, "-y", "-i", video_path, 
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", temp_wav
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("\n[2/4] Faster-Whisper로 대사 스크립트 작성 중...")
    model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    segments, info = model.transcribe(temp_wav, beam_size=5, language="ko", vad_filter=True)
    
    raw_segments = []
    for segment in segments:
        print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")
        raw_segments.append({
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip()
        })
        
    if not raw_segments:
        print("대사가 감지되지 않았습니다. 종료합니다.")
        return

    print(f"\n[3/4] SBERT(Ko-sRoBERTa) 대사 의미(Context) 군집화 중... (총 {len(raw_segments)} 문장)")
    bert_model = SentenceTransformer('jhgan/ko-sroberta-multitask')
    
    contexts = []
    current_context = [raw_segments[0]]
    
    for i in range(1, len(raw_segments)):
        prev = raw_segments[i-1]
        curr = raw_segments[i]
        
        # 문장 간 침묵 시간이 4.0초 이상 벌어지면 완전히 새로운 씬/상황으로 판단 후 분리
        if curr["start"] - prev["end"] > 4.0:
            contexts.append(current_context)
            current_context = [curr]
            continue
            
        emb1 = bert_model.encode([prev["text"]])
        emb2 = bert_model.encode([curr["text"]])
        sim = cosine_similarity(emb1, emb2)[0][0]
        
        # 두 문장이 맥락상 연관성이 30% 미만이면 완전히 대화 주제가 바뀐 것으로 간주!
        if sim < 0.3:
            contexts.append(current_context)
            current_context = [curr]
        else:
            current_context.append(curr)
            
    contexts.append(current_context)

    print(f"\n[4/4] 총 {len(contexts)}개의 문맥 덩어리로 원본 영상 분할(Split) 및 CSV 기록 중...")
    csv_data = [["Filename", "Start Time(s)", "End Time(s)", "Context Text"]]
    
    for idx, ctx in enumerate(contexts):
        c_start = ctx[0]["start"]
        c_end = ctx[-1]["end"]
        
        # 말이 너무 딱딱하게 잘리는 것을 막기 위해 오디오 패딩 부여 (시작 -0.3초, 끝 +0.5초)
        pad_start = max(0, c_start - 0.3)
        pad_end = c_end + 0.5
        c_text = " ".join([seg["text"] for seg in ctx])
        
        out_filename = f"{basename}_context_{idx+1:04d}.mp4"
        out_filepath = os.path.join(output_dir, out_filename)
        
        csv_data.append([
            out_filename,
            f"{c_start:.2f}",
            f"{c_end:.2f}",
            c_text
        ])
        
        # Scenedetect로 자른 파일 합치지 않고!
        # 원본 영상에서 직접 시간(Time-stamp)값으로 매우 정밀하게 도려내서 독립된 mp4로 추출 (재인코딩 없이 고속 copy)
        cmd = [
            ffmpeg_exe, "-y", 
            "-ss", str(pad_start), 
            "-to", str(pad_end),
            "-i", video_path, 
            "-c", "copy", "-avoid_negative_ts", "make_zero", out_filepath
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f" -> {out_filename} 분할 생성 완료 (실제 대사: {c_start:.2f}s ~ {c_end:.2f}s)")

    # 추출된 메타데이터 CSV를 따로 보존
    csv_path = os.path.join(output_dir, f"{basename}_metadata.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as cf:
        csv.writer(cf).writerows(csv_data)

    print(f"\n[모든 파이프라인 구동 완료] 결과 데이터가 {output_dir} 경로에 안전하게 저장되었습니다!")

if __name__ == "__main__":
    # 명령어 실행 시 직접 옵션을 줄 수 있도록 범용적인 argparse 옵션 제공
    parser = argparse.ArgumentParser(description="대사 추출 및 문맥 단위 독립적 영상 절단 파이프라인")
    parser.add_argument("--video", "-v", default=r"data\videos\2_short.mp4", help="분석할 원본 비디오 경로")
    parser.add_argument("--output", "-o", default=r"outputs\2_short_analysis", help="분할 영상과 CSV를 저장할 개별 출력 폴더")
    args = parser.parse_args()
    
    # 윈도우 환경에 맞게 경로 재변환
    video_path = os.path.normpath(args.video)
    output_dir = os.path.normpath(args.output)
    
    if not os.path.exists(video_path):
         print(f"오류: 입력하신 영상 파일 '{video_path}'을 찾을 수 없습니다. (경로를 확인하세요!)")
         exit(1)
         
    chunk_video_by_context(video_path, output_dir)
