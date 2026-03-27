/** Single overlay entry returned by the backend API (Step 5). */
export interface OverlayEntry {
  decision_id: number;       // 피드백 제출 시 사용 (ad_placement_feedback FK)
  matched_ad_id: string;
  ad_resource_url: string;
  ad_type: "video_clip" | "banner";
  overlay_start_time_sec: number;
  overlay_duration_sec: number;
  coordinates_x: number | null;
  coordinates_y: number | null;
  coordinates_w: number | null;
  coordinates_h: number | null;
  score: number;
}

/** Full overlay metadata response from GET /overlay/{job_id}. */
export interface OverlayMetadata {
  job_id: string;
  original_video_url: string;
  total_duration_sec: number;
  overlays: OverlayEntry[];
}

/** Job status from GET /jobs/{job_id}. */
export interface JobStatus {
  job_id: string;
  status: string;
  input_video_path: string;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}
