from scenedetect import detect, ContentDetector
from scenedetect.video_splitter import split_video_ffmpeg
import os

def split_video_into_scenes(video_path: str, output_dir: str):
    """
    비디오 파일에서 씬 전환을 감지하여 분할하고 지정된 경로에 각 씬을 영상 파일로 저장합니다.
    (메모리 누수 및 에러 방지를 위해 moviepy 대신 ffmpeg를 직접 활용하는 빠르고 안전한 방식을 사용합니다.)
    """
    print(f"[{video_path}] 에서 씬 분할을 시작합니다...")
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # 1. scenedetect를 사용하여 씬(장면) 전환 시간 감지
        print("씬을 감지하고 있습니다. (영상 길이에 따라 시간이 소요됩니다)")
        scene_list = detect(video_path, ContentDetector())
        
        if not scene_list:
            print("비디오에서 분할할 씬을 찾지 못했습니다.")
            return False
            
        print(f"총 {len(scene_list)}개의 씬이 감지되었습니다. 분할 저장을 시작합니다...")

        # 3. 감지된 씬 구간을 바탕으로 ffmpeg를 사용하여 비디오 잘라내기
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        
        # 현재 위치를 출력 폴더로 변경하여 파일이 직접 저장되도록 유도
        original_cwd = os.getcwd()
        os.chdir(output_dir)
        
        # ffmpeg을 이용한 비디오 분할
        video_path_abs = os.path.join(original_cwd, video_path) if not os.path.isabs(video_path) else video_path
        split_video_ffmpeg(video_path_abs, scene_list, output_file_template=f"{base_name}_scene_$SCENE_NUMBER.mp4", show_output=False)
        
        # 원래 위치로 복귀
        os.chdir(original_cwd)
        
        print(f"씬 분할 및 저장이 성공적으로 완료되었습니다. 저장 폴더: {output_dir}")
        return True
    except Exception as e:
        print(f"씬 분할 중 오류 발생: {e}")
        return False

if __name__ == "__main__":
    split_video_into_scenes("SampleVideo.mp4", "SampleVideo_Scenes")
