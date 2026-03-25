import os
from moviepy import VideoFileClip
from scenedetect import detect, ContentDetector
from scenedetect.video_splitter import split_video_ffmpeg

def extract_audio(video_path: str, output_audio_path: str):
    """
    비디오 파일에서 오디오 스트림만 추출하여 지정된 경로에 WAV 파일로 저장합니다.
    (나중에 오디오(대사) 기반 Whisper STT 처리에 사용됩니다.)
    """
    print(f"[{video_path}] 에서 오디오 추출을 시작합니다...")
    try:
        # 1. moviepy를 사용하여 비디오 클립 로드
        video_clip = VideoFileClip(video_path)
        audio_clip = video_clip.audio
        
        # 오디오 트랙이 존재하지 않는 비디오일 경우 에러 방지 처리
        if audio_clip is None:
             print("비디오에 오디오 트랙이 포함되어 있지 않습니다.")
             return False
             
        # 2. Whisper 모델에서 가장 잘 인식할 수 있는 .wav 포맷으로 오디오만 우선 저장
        # (불필요한 로그 출력을 끄기 위해 logger=None 설정)
        audio_clip.write_audiofile(output_audio_path, logger=None)
        
        # 3. 메모리 누수(Memory leak) 방지를 위해 사용이 끝난 클립 리소스 안전하게 닫기
        audio_clip.close()
        video_clip.close()
        
        print(f"오디오 추출이 완료되었습니다. 저장 경로: {output_audio_path}")
        return True
    except Exception as e:
        print(f"오디오 추출 중 오류 발생: {e}")
        return False

def detect_and_split_scenes(video_path: str, output_dir: str):
    """
    영상 내에서 의미 있는 화면(Scene) 전환을 감지하고,
    해당 전환점을 기준으로 원본 영상을 분할하여 각각 독립된 mp4 파일로 저장합니다.
    """
    print(f"[{video_path}] 에서 씬(Scene) 감지 및 분할을 시작합니다...")
    try:
        # 1. scenedetect의 ContentDetector를 사용하여 영상 내 씬 전환 시점 감지
        # threshold(기본값 27.0): 값이 낮을수록 미세한 화면 변화에도 씬을 나누고, 높을수록 아주 큰 화면 변화에만 씬을 나눕니다.
        scene_list = detect(video_path, ContentDetector(threshold=27.0))
        
        # 감지된 씬이 없는 경우 (예: 화면 변화가 없는 고정 카메라 영상이거나 길이가 너무 짧은 경우)
        if not scene_list:
            print("감지된 화면 전환이 없거나, 영상이 너무 짧습니다.")
            return []
            
        print(f"총 {len(scene_list)}개의 화면 전환(씬)이 발견되었습니다. 영상을 분할합니다...")
        
        # 2. ffmpeg 백엔드를 호출하여, 원본 영상을 위에서 얻은 씬 리스트(scene_list) 기준으로 실제 디스크에 자르고 저장
        # 이전 WinError 방지를 위해 디렉토리 이동 방식을 사용
        original_cwd = os.getcwd()
        os.chdir(output_dir)
        
        video_path_abs = os.path.join(original_cwd, video_path) if not os.path.isabs(video_path) else video_path
        
        split_video_ffmpeg(video_path_abs, scene_list, output_file_template="scene-$SCENE_NUMBER.mp4", show_progress=True)
        
        os.chdir(original_cwd)
        
        print(f"씬(Scene) 단위 영상 분할이 성공적으로 완료되었습니다. 저장 디렉토리: {output_dir}")
        return scene_list
        
    except Exception as e:
        print(f"씬 분할 과정 중 오류 발생 부분: {e}")
        # 오류 발생 시 원래 디렉토리로 확실히 복귀
        if 'original_cwd' in locals() and os.getcwd() != original_cwd:
            os.chdir(original_cwd)
        return []

if __name__ == "__main__":
    BASE_DIR = os.getcwd()
    TEST_VIDEO = os.path.join(BASE_DIR, "SampleVideo.mp4")
    
    # 요청에 따라 SampleVideo_Scenes2 폴더 생성
    PROCESSED_DIR = os.path.join(BASE_DIR, "SampleVideo_Scenes2")
    AUDIO_DIR = os.path.join(PROCESSED_DIR, "audio")
    SCENE_DIR = os.path.join(PROCESSED_DIR, "scenes")
    
    # 결과물을 저장할 폴더 생성 (exist_ok=True)
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(SCENE_DIR, exist_ok=True)
    
    print("=== [Contextual Video Ad Insertion] 전처리(Preprocessing) 파이프라인 시작 ===")
    
    # [Step 1] 오디오 추출
    output_audio = os.path.join(AUDIO_DIR, "extracted_audio.wav")
    extract_audio(TEST_VIDEO, output_audio)
    
    # [Step 2] 비디오 씬 분할
    detect_and_split_scenes(TEST_VIDEO, SCENE_DIR)
    
    print("=== 전처리(Preprocessing) 파이프라인 무사히 종료되었습니다! ===")
