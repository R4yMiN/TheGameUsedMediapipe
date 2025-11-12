import sys
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage
import cv2
import mediapipe as mp
import math
import numpy as np
import time

# 初始化 MediaPipe Hands 解决方案
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils


# --- 指数移动平均 (EMA) 滤波器 ---
class EMAFilter:
    """用于平滑关键点坐标的滤波器。"""

    def __init__(self, alpha=0.3):
        self.alpha = alpha
        self.last_x = None
        self.last_y = None

    def filter(self, new_x, new_y):
        if self.last_x is None:
            self.last_x = new_x
            self.last_y = new_y
        else:
            self.last_x = self.alpha * new_x + (1 - self.alpha) * self.last_x
            self.last_y = self.alpha * new_y + (1 - self.alpha) * self.last_y
        return self.last_x, self.last_y


# --- 视频工作线程 ---
class VideoWorker(QThread):
    """独立线程，用于处理耗时的视频捕获和 MediaPipe 姿态识别。"""
    image_ready = pyqtSignal(QPixmap)
    coords_ready = pyqtSignal(float, float)
    gesture_ready = pyqtSignal(bool)  # 握拳状态 (True=握拳/点击/绘画)
    page_flick_ready = pyqtSignal(int)  # 拇指-小指捏合 (翻页/切换选项)
    return_home_ready = pyqtSignal()  # !!! 新增：拇指-中指捏合 (返回主页) !!!

    def __init__(self):
        super().__init__()
        self.running = True
        self.filter_index = EMAFilter(alpha=0.3)

        # --- 手势检测阈值 ---
        self.GRASP_THRESHOLD = 0.20  # 握拳 (Click/Draw)

        # 返回主页 (拇指-中指捏合)
        self.PINCH_THRESHOLD = 0.04  # 捏合距离阈值，比翻页更紧
        self.PINCH_COOLDOWN = 1.0  # 冷却时间
        self.last_pinch_time = 0
        self.is_pinching = False

        # 翻页手势 (拇指-小指捏合)
        self.FLICK_THRESHOLD = 0.07  # 拇指小指捏合阈值，比捏合松一些
        self.FLICK_COOLDOWN = 1.0
        self.last_flick_time = 0
        self.is_flicking = False

        # 追踪点稳定性补偿参数
        self.Y_COMPENSATION_OFFSET = 0.04

    def stop(self):
        """安全停止线程。"""
        self.running = False
        self.wait()

    def run(self):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("错误: 无法打开摄像头！")
            self.running = False
            return

        with mp_hands.Hands(
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
                max_num_hands=2) as hands:

            while self.running:
                success, frame = cap.read()
                if not success:
                    continue

                frame = cv2.flip(frame, 1)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rgb_frame.flags.writeable = False

                results = hands.process(rgb_frame)

                rgb_frame.flags.writeable = True
                current_time = time.time()

                pinch_pose_detected = False
                flick_pose_detected = False

                if results.multi_hand_landmarks:
                    hand_landmarks_cursor = results.multi_hand_landmarks[0]

                    # 1. 握拳/静息手势检测 (Click/Draw)
                    palm_point = hand_landmarks_cursor.landmark[mp_hands.HandLandmark.WRIST]
                    finger_tips_indices = [
                        mp_hands.HandLandmark.INDEX_FINGER_TIP, mp_hands.HandLandmark.MIDDLE_FINGER_TIP,
                        mp_hands.HandLandmark.RING_FINGER_TIP, mp_hands.HandLandmark.PINKY_TIP
                    ]

                    total_distance = 0
                    for tip_index in finger_tips_indices:
                        tip_point = hand_landmarks_cursor.landmark[tip_index]
                        distance = math.sqrt(
                            (tip_point.x - palm_point.x) ** 2 +
                            (tip_point.y - palm_point.y) ** 2 +
                            (tip_point.z - palm_point.z) ** 2
                        )
                        total_distance += distance

                    average_distance = total_distance / len(finger_tips_indices)

                    is_grasping = average_distance < self.GRASP_THRESHOLD
                    self.gesture_ready.emit(is_grasping)

                    # 2. 追踪坐标 (食指指尖 LandMark 8)
                    index_tip = hand_landmarks_cursor.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP]
                    x_raw = index_tip.x
                    y_raw = index_tip.y

                    # --- Y 轴追踪点稳定性补偿 ---
                    if is_grasping:
                        y_raw -= self.Y_COMPENSATION_OFFSET

                    # 应用 EMA 平滑
                    x_filtered, y_filtered = self.filter_index.filter(x_raw, y_raw)
                    self.coords_ready.emit(x_filtered, y_filtered)

                    # 3. 捏合手势检测 (返回主页)

                    # 拇指尖 (THUMB_TIP) 和中指尖 (MIDDLE_FINGER_TIP) 的距离
                    thumb_tip = hand_landmarks_cursor.landmark[mp_hands.HandLandmark.THUMB_TIP]
                    middle_tip = hand_landmarks_cursor.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_TIP]

                    pinch_distance = math.sqrt(
                        (thumb_tip.x - middle_tip.x) ** 2 +
                        (thumb_tip.y - middle_tip.y) ** 2 +
                        (thumb_tip.z - middle_tip.z) ** 2
                    )

                    is_current_pinch = pinch_distance < self.PINCH_THRESHOLD

                    if is_current_pinch:
                        pinch_pose_detected = True

                        # 确保不处于握拳状态，且处于冷却期外
                        if not is_grasping and not self.is_pinching and (
                                current_time - self.last_pinch_time > self.PINCH_COOLDOWN):
                            self.return_home_ready.emit()
                            print("--- 拇指-中指捏合：返回主页触发！ ---")
                            self.last_pinch_time = current_time
                            self.is_pinching = True

                    if not pinch_pose_detected and self.is_pinching:
                        self.is_pinching = False

                    # 4. 翻页手势检测 (拇指-小指捏合)
                    for idx, hand_landmarks in enumerate(results.multi_hand_landmarks):
                        # 仅处理用于翻页的非主力手（可选）
                        handedness = results.multi_handedness[idx].classification[0].label
                        thumb_tip = hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP]
                        pinky_tip = hand_landmarks.landmark[mp_hands.HandLandmark.PINKY_TIP]

                        flick_distance = math.sqrt(
                            (thumb_tip.x - pinky_tip.x) ** 2 +
                            (thumb_tip.y - pinky_tip.y) ** 2 +
                            (thumb_tip.z - pinky_tip.z) ** 2
                        )

                        is_current_flick = flick_distance < self.FLICK_THRESHOLD

                        if is_current_flick:
                            flick_pose_detected = True

                            if not self.is_flicking and (current_time - self.last_flick_time > self.FLICK_COOLDOWN):

                                if handedness == 'Right':
                                    self.page_flick_ready.emit(1)
                                    print("--- 翻页事件触发: 右手下一页 ---")
                                elif handedness == 'Left':
                                    self.page_flick_ready.emit(-1)
                                    print("--- 翻页事件触发: 左手上一页 ---")

                                self.last_flick_time = current_time
                                self.is_flicking = True
                                break

                    if not flick_pose_detected and self.is_flicking:
                        self.is_flicking = False

                    # 绘制关键点和连接线
                    for hand_landmarks in results.multi_hand_landmarks:
                        mp_drawing.draw_landmarks(
                            rgb_frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)

                else:
                    self.is_pinching = False
                    self.is_flicking = False

                    # 5. 图像信号发送
                h, w, ch = rgb_frame.shape
                bytes_per_line = ch * w
                qt_image = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
                self.image_ready.emit(QPixmap.fromImage(qt_image))

        cap.release()
        print("VideoWorker 线程已释放摄像头。")