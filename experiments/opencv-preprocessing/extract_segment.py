from moviepy import VideoFileClip
import os

def extract_video_segment(input_file, output_file, start_time, end_time):
    """
    Extracts a segment from a video file using moviepy.
    
    :param input_file: Path to the input video file.
    :param output_file: Path to save the extracted segment.
    :param start_time: Start time in seconds or (min, sec).
    :param end_time: End time in seconds or (min, sec).
    """
    try:
        print(f"Loading video: {input_file}")
        with VideoFileClip(input_file) as video:
            print(f"Extracting segment from {start_time} to {end_time}...")
            # subclip works with seconds or (min, sec)
            new_clip = video.subclipped(start_time, end_time)
            
            # Writing the result to a file
            # codec='libx264' is common, but let's try to keep it simple
            new_clip.write_videofile(output_file, codec='libx264', audio_codec='aac')
            
        print(f"Successfully extracted segment to {output_file}")
        
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == "__main__":
    input_video = "나는 SOLO.E243.260304.720p-NEXT.mp4"
    output_video = "Solo_Sample.mp4"
    
    # 40:00 = 2400 seconds
    # 49:44 = 2984 seconds
    start = 2400
    end = 2984
    
    if os.path.exists(input_video):
        extract_video_segment(input_video, output_video, start, end)
    else:
        print(f"Input file not found: {input_video}")
