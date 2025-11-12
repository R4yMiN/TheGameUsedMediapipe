import sys
import random
import math
import os
from PIL import Image  # 用于在程序启动时切割贴图，假设已安装

from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QGraphicsView, QGraphicsScene, \
    QGraphicsPixmapItem, QGraphicsRectItem
from PyQt5.QtCore import Qt, QTimer, QPointF
from PyQt5.QtGui import QColor, QBrush, QPen, QFont, QPixmap, QPainter, QImage

# 引入追踪核心
from tracker_worker import VideoWorker

# --- 常量定义 ---
GAME_WIDTH = 1200
GAME_HEIGHT = 800

# 水果忍者相关常量
FRUIT_SIZE = 64
GRAVITY = 0.5

# 绘画应用常量
PAPER_COLOR = QColor(255, 250, 240)  # 柔和的纸张色 (Floral White)
COLOR_CHOICES = {
    'Red': QColor(255, 0, 0),
    'Green': QColor(0, 255, 0),
    'Blue': QColor(0, 0, 255),
    'Yellow': QColor(255, 255, 0),
    'Eraser': PAPER_COLOR,  # 橡皮擦 (与背景色一致)
}
COLOR_ORDER = list(COLOR_CHOICES.keys())
COLOR_TILE_SIZE = 50
COLOR_AREA_WIDTH = COLOR_TILE_SIZE * 2  # 预留给颜色选择器和尺寸选择器的宽度

# 画笔尺寸定义
PEN_SIZES = {
    'Small': 8,
    'Medium': 18,
    'Large': 36
}
SIZE_ORDER = list(PEN_SIZES.keys())
SIZE_TILE_SIZE = 30  # 尺寸切换按钮的显示大小


# --- 核心游戏对象：水果忍者类 ---

class BaseGameItem(QGraphicsPixmapItem):
    """场景中移动物体的基类"""

    def __init__(self, pixmap, name):
        super().__init__(pixmap)
        self.name = name
        self.velocity = QPointF(0, 0)
        self.gravity_factor = 1.0


class FruitItem(BaseGameItem):
    """完整的、可以被切割的水果"""

    def __init__(self, pixmap, name):
        super().__init__(pixmap, name)
        self.is_sliced = False
        self.original_name = name


class SliceItem(BaseGameItem):
    """切割后的碎片"""

    def __init__(self, pixmap, name):
        super().__init__(pixmap, name)
        self.is_sliced = True
        self.gravity_factor = 1.5  # 碎片受重力影响更大


# --- 主窗口类 ---
class GameWindow(QMainWindow):
    """
    主程序窗口，管理游戏状态、UI 渲染和信号连接。
    """

    def __init__(self):
        super().__init__()

        print("--- 1. 游戏主程序开始初始化 ---")

        self.setWindowTitle("PyQt MediaPipe Game Launcher")
        self.setGeometry(100, 100, GAME_WIDTH, GAME_HEIGHT)

        # 核心状态管理: 'launcher', 'drawing_app', 'fruit_slicer'
        self.game_state = 'launcher'

        # --- 资源预处理：水果切割 (仅需在启动时执行一次) ---
        self._slice_assets()

        # --- 场景和视图 ---
        self.scene = QGraphicsScene(0, 0, GAME_WIDTH, GAME_HEIGHT)
        self.game_view = QGraphicsView(self.scene)
        self.game_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.game_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCentralWidget(self.game_view)

        # --- UI元素和控制器 ---
        self.knife_cursor = None
        self.video_label = None
        self.coord_display = None
        self._setup_ui_elements()

        # --- 通用逻辑变量 ---
        self.is_grasping = False
        self.cursor_pos = QPointF(0, 0)
        self.knife_trail = []
        self.max_trail_length = 5

        # --- Launcher 逻辑变量 ---
        self.game_list = ["Drawing App (体感绘画板)", "Fruit Slicer (体感水果忍者)", "Exit Game (退出)"]
        self.current_selection = 0
        self.launcher_items = []
        self._setup_launcher()

        # --- 游戏模式特定变量 ---
        self.active_fruits = []
        self.drawing_canvas = None
        self.canvas_pixmap = None
        self.current_color = COLOR_CHOICES['Red']
        self.current_pen_size = PEN_SIZES['Medium']
        self.color_tiles: list[QGraphicsRectItem] = []
        self.size_tiles: list[QGraphicsRectItem] = []
        self.last_draw_pos = None

        # --- 游戏循环和水果生成定时器 ---
        self._setup_timers()

        # --- 启动追踪线程并连接信号 ---
        self.worker = VideoWorker()
        self.worker.image_ready.connect(self.update_video)
        self.worker.coords_ready.connect(self.update_knife_position)
        self.worker.gesture_ready.connect(self.handle_grasp_state)
        self.worker.page_flick_ready.connect(self.handle_flick_gesture)
        # 连接：拇指-中指捏合返回主页
        self.worker.return_home_ready.connect(lambda: self.switch_to_game_mode('launcher'))

        self.worker.start()
        print("--- 2. VideoWorker 线程已启动 ---")

    # --- 辅助方法：资源切割 (水果忍者依赖) ---

    def _slice_assets(self):
        """预切割水果贴图，用于水果忍者游戏。"""
        print("--- 3. 正在进行资源预切割... ---")
        FULL_ATLAS_PATH = "assets/Fruits.png"
        SLICES_ATLAS_PATH = "assets/Slices.png"
        self.full_output_dir = "assets/full_fruits"
        self.slices_output_dir = "assets/slices"

        os.makedirs(self.full_output_dir, exist_ok=True)
        os.makedirs(self.slices_output_dir, exist_ok=True)

        full_fruit_map = {
            (0, 0, 32, 32): "apple", (32, 0, 32, 32): "pear", (64, 0, 32, 32): "lemon",
        }

        self.slice_parts = {
            "apple": ["left", "right"], "pear": ["left", "right"], "lemon": ["half1", "half2"],
        }

        slice_map = {
            (0, 0, 32, 16): "apple_slice_left", (32, 0, 32, 16): "apple_slice_right",
            (64, 0, 32, 16): "pear_slice_left", (96, 0, 32, 16): "pear_slice_right",
            (64, 48, 32, 32): "lemon_slice_half1", (96, 48, 32, 32): "lemon_slice_half2",
        }

        try:
            atlas_img = Image.open(FULL_ATLAS_PATH)
            for (x, y, w, h), name in full_fruit_map.items():
                tile = atlas_img.crop((x, y, x + w, y + h))
                tile.save(os.path.join(self.full_output_dir, f"{name}_full.png"))

            slices_img = Image.open(SLICES_ATLAS_PATH)
            for (x, y, w, h), name in slice_map.items():
                tile = slices_img.crop((x, y, x + w, y + h))
                tile.save(os.path.join(self.slices_output_dir, f"{name}.png"))

            print("--- 4. 资源预切割完成！ ---")
        except FileNotFoundError:
            print(f"警告：未找到图集文件，水果忍者模式可能无法加载贴图。请确保 assets/ 文件夹存在。")
        except Exception as e:
            print(f"警告：切割贴图失败。错误: {e}")

    def _setup_ui_elements(self):
        """初始化跨模式 UI 元素，重点优化光标可见性。"""
        # 摄像头视频显示
        self.video_label = QLabel(self)
        self.video_label.setGeometry(20, 20, 320, 240)
        self.video_label.setStyleSheet("border: 3px solid red; background-color: black;")
        self.video_label.setParent(self.game_view)

        # 追踪光标 (高可见度设计：实心白 + 黑边)
        self.knife_cursor = self.scene.addEllipse(0, 0, 20, 20,
                                                  QPen(QColor(0, 0, 0), 2),  # 2px 粗黑边
                                                  QBrush(QColor(255, 255, 255)))  # 实心白色
        self.knife_cursor.setZValue(100)

        # 坐标显示
        self.coord_display = QLabel(self)
        self.coord_display.setGeometry(20, 280, 320, 40)
        self.coord_display.setFont(QFont("Arial", 12))
        self.coord_display.setStyleSheet("color: blue;")
        self.coord_display.setParent(self.game_view)

    def _setup_launcher(self):
        """设置游戏启动器界面 (Launcher)。"""
        title_font = QFont("Arial", 40, QFont.Bold)
        item_font = QFont("Arial", 24)

        # 标题
        title = self.scene.addText("PyQt 体感游戏中心", title_font)
        title.setDefaultTextColor(QColor(255, 255, 255))
        title.setPos(GAME_WIDTH / 2 - title.boundingRect().width() / 2, 100)
        self.launcher_items.append(title)

        # 游戏选项
        y_pos = 250
        for i, name in enumerate(self.game_list):
            item = self.scene.addText(name, item_font)
            item.setDefaultTextColor(QColor(200, 200, 200))
            item.setPos(GAME_WIDTH / 2 - item.boundingRect().width() / 2, y_pos + i * 80)
            self.launcher_items.append(item)

        self.update_launcher_selection()

        # --- 游戏模式初始化/清理 ---

    def _setup_drawing_app(self):
        """设置绘画应用界面（画布、颜色和尺寸选择器）。"""
        canvas_width = GAME_WIDTH - COLOR_AREA_WIDTH
        canvas_height = GAME_HEIGHT

        self.canvas_pixmap = QPixmap(canvas_width, canvas_height)
        self.canvas_pixmap.fill(PAPER_COLOR)  # 使用柔和的纸张色

        self.drawing_canvas = self.scene.addPixmap(self.canvas_pixmap)
        self.drawing_canvas.setPos(0, 0)
        self.drawing_canvas.setZValue(0)

        # 设置颜色选择器 (右侧边缘，上方)
        y_offset_color = 50
        self.color_tiles = []
        for i, color_name in enumerate(COLOR_ORDER):
            color = COLOR_CHOICES[color_name]

            tile = self.scene.addRect(
                canvas_width + (COLOR_AREA_WIDTH - COLOR_TILE_SIZE) / 2,
                y_offset_color + i * (COLOR_TILE_SIZE + 10),
                COLOR_TILE_SIZE,
                COLOR_TILE_SIZE,
                QPen(QColor(100, 100, 100), 1),
                QBrush(color)
            )
            tile.setZValue(10)
            tile.color_name = color_name
            self.color_tiles.append(tile)

        # 设置尺寸选择器 (右侧边缘，下方)
        y_offset_size = y_offset_color + len(COLOR_ORDER) * (COLOR_TILE_SIZE + 10) + 30  # 在颜色下方留出空间
        self.size_tiles = []
        for i, size_name in enumerate(SIZE_ORDER):
            size_val = PEN_SIZES[size_name]

            tile = self.scene.addRect(
                canvas_width + (COLOR_AREA_WIDTH - SIZE_TILE_SIZE) / 2,
                y_offset_size + i * (SIZE_TILE_SIZE + 10),
                SIZE_TILE_SIZE,
                SIZE_TILE_SIZE,
                QPen(QColor(100, 100, 100), 1),
                QBrush(QColor(200, 200, 200))
            )

            # 在方块中央绘制一个圆点来表示尺寸
            dot = self.scene.addEllipse(
                0, 0, size_val, size_val, QPen(Qt.NoPen), QBrush(Qt.black)
            )
            dot.setPos(tile.pos().x() + (SIZE_TILE_SIZE - size_val) / 2,
                       tile.pos().y() + (SIZE_TILE_SIZE - size_val) / 2)

            tile.setZValue(10)
            tile.size_name = size_name
            tile.pen_size = size_val
            tile.dot_item = dot  # 关联圆点
            self.size_tiles.append(tile)

        self.last_draw_pos = None
        self._update_pen_size_selection()  # 初始化尺寸选择高亮

    def _cleanup_drawing_app(self):
        """清理绘画应用界面元素。"""
        if self.drawing_canvas:
            self.scene.removeItem(self.drawing_canvas)
            self.drawing_canvas = None
        for tile in self.color_tiles:
            self.scene.removeItem(tile)
        self.color_tiles = []
        # 清理尺寸选择器
        for tile in self.size_tiles:
            self.scene.removeItem(tile.dot_item)  # 移除圆点
            self.scene.removeItem(tile)
        self.size_tiles = []

        self.last_draw_pos = None
        self.current_color = COLOR_CHOICES['Red']
        self.current_pen_size = PEN_SIZES['Medium']

    def _cleanup_fruit_slicer(self):
        """清理水果忍者模式的元素。"""
        self.spawn_timer.stop()
        for item in list(self.active_fruits):
            self.scene.removeItem(item)
            self.active_fruits.remove(item)

    def switch_to_game_mode(self, mode):
        """切换主程序状态，并执行模式特定的初始化/清理。"""

        # 1. 清理上一个模式的UI
        if self.game_state == 'drawing_app':
            self._cleanup_drawing_app()
        elif self.game_state == 'fruit_slicer':
            self._cleanup_fruit_slicer()

        # 2. 设置新模式
        self.game_state = mode

        if mode == 'drawing_app':
            print("--- 启动：体感绘画板 ---")
            self._show_launcher(False)
            self.setWindowTitle("PyQt MediaPipe Drawing App")
            self._setup_drawing_app()

        elif mode == 'fruit_slicer':
            print("--- 启动：体感水果忍者 ---")
            self._show_launcher(False)
            self.setWindowTitle("PyQt MediaPipe Fruit Slicer")
            self.spawn_timer.start(random.randint(1000, 3000))

        elif mode == 'launcher':
            print("--- 返回：游戏中心 ---")
            self._show_launcher(True)
            self.setWindowTitle("PyQt MediaPipe Game Launcher")

    # --- Launcher 逻辑 ---

    def _show_launcher(self, show):
        """显示/隐藏 Launcher 元素。"""
        for item in self.launcher_items:
            item.setVisible(show)

        if show:
            self.update_launcher_selection()

    def update_launcher_selection(self):
        """根据当前选择更新 Launcher 选项的高亮样式。"""
        # 跳过标题，从索引 1 开始
        for i in range(1, len(self.launcher_items)):
            item = self.launcher_items[i]

            if (i - 1) == self.current_selection:
                item.setDefaultTextColor(QColor(255, 255, 0))  # 黄色高亮
                item.setFont(QFont("Arial", 24, QFont.Bold))
            else:
                item.setDefaultTextColor(QColor(200, 200, 200))
                item.setFont(QFont("Arial", 24))

    def _update_pen_size_selection(self):
        """根据当前尺寸更新尺寸方块的高亮样式。"""
        for tile in self.size_tiles:
            if tile.pen_size == self.current_pen_size:
                tile.setPen(QPen(QColor(255, 255, 0), 3))  # 选中时黄色粗边
            else:
                tile.setPen(QPen(QColor(100, 100, 100), 1))  # 未选中时细边

    def handle_flick_gesture(self, direction):
        """处理翻页手势（用于 Launcher 模式切换选项）。"""
        if self.game_state != 'launcher':
            return

        num_games = len(self.game_list)
        self.current_selection = (self.current_selection + direction) % num_games
        if self.current_selection < 0:
            self.current_selection += num_games

        self.update_launcher_selection()
        print(f"Launcher 切换: {self.game_list[self.current_selection]}")

    def handle_grasp_state(self, is_grasping):
        """处理握拳/静息状态，并更新光标。"""

        was_grasping = self.is_grasping
        self.is_grasping = is_grasping

        if self.is_grasping:
            # 握拳：光标显示当前笔刷/刀的颜色
            grasp_brush = QBrush(self.current_color)
            self.knife_cursor.setBrush(grasp_brush)
            self.knife_cursor.setPen(QPen(QColor(0, 0, 0), 2))  # 保持黑边

            if not was_grasping:
                if self.game_state == 'launcher':
                    # 只有从静息 -> 握拳 (点击动作) 时才检查
                    self.check_launcher_click()
                elif self.game_state == 'drawing_app':
                    self.check_color_and_size_pickup()

        else:
            # 静息：光标恢复高可见度状态
            rest_brush = QBrush(QColor(255, 255, 255))
            self.knife_cursor.setBrush(rest_brush)
            self.knife_cursor.setPen(QPen(QColor(0, 0, 0), 2))

            self.last_draw_pos = None  # 停止绘制

    def check_launcher_click(self):
        """
        检测 Launcher 选项点击。
        !!! 关键修复：直接启动当前选中的游戏，忽略光标位置，将握拳视为确认。!!!
        """
        # +1 跳过标题
        if self.current_selection + 1 >= len(self.launcher_items):
            return

        # 1. 获取当前选中的游戏名称
        game_name = self.game_list[self.current_selection]

        # 2. 直接启动/执行该选项
        self.start_game(game_name)

    def check_color_and_size_pickup(self):
        """在 Drawing App 模式下，检查是否点击了颜色或尺寸方块。"""
        cursor_rect = self.knife_cursor.sceneBoundingRect()

        # 检查颜色拾取
        for tile in self.color_tiles:
            if cursor_rect.intersects(tile.sceneBoundingRect()):
                self.current_color = COLOR_CHOICES[tile.color_name]
                print(f"颜色拾取: {tile.color_name}")
                self.knife_cursor.setBrush(QBrush(self.current_color))
                return

        # 检查尺寸拾取
        for tile in self.size_tiles:
            if cursor_rect.intersects(tile.sceneBoundingRect()):
                self.current_pen_size = tile.pen_size
                print(f"尺寸拾取: {tile.size_name} ({self.current_pen_size}px)")
                self._update_pen_size_selection()  # 更新高亮
                return

    def start_game(self, name):
        """根据名称启动对应的游戏或操作。"""
        if name == "Drawing App (体感绘画板)":
            self.switch_to_game_mode('drawing_app')
        elif name == "Fruit Slicer (体感水果忍者)":
            self.switch_to_game_mode('fruit_slicer')
        elif name == "Exit Game (退出)":
            # 退出操作
            self.close()
        else:
            print(f"游戏 {name} 尚未实现")

    # --- 游戏循环和定时器 ---

    def _setup_timers(self):
        # 主游戏循环 (60 FPS)
        self.game_timer = QTimer(self)
        self.game_timer.timeout.connect(self.game_loop)
        self.game_timer.start(1000 // 60)

        # 水果生成定时器 (仅在水果忍者模式下启动)
        self.spawn_timer = QTimer(self)
        self.spawn_timer.timeout.connect(self.spawn_random_fruit)

    def game_loop(self):
        """每帧执行一次：执行当前模式下的主要逻辑。"""

        if self.game_state == 'drawing_app':
            self.draw_on_canvas()

        elif self.game_state == 'fruit_slicer':
            self._update_fruits()

        if len(self.knife_trail) > self.max_trail_length:
            self.knife_trail.pop(0)

    # --- Drawing App 逻辑 ---

    def draw_on_canvas(self):
        """在 Drawing App 模式下，根据握拳状态在画布上绘制。"""

        if not self.is_grasping or self.drawing_canvas is None:
            self.last_draw_pos = None
            return

        current_pos = self.cursor_pos
        canvas_width = self.drawing_canvas.pixmap().width()

        if current_pos.x() >= canvas_width:
            self.last_draw_pos = None
            return

        if self.last_draw_pos is None:
            self.last_draw_pos = current_pos
            return

        painter = QPainter(self.canvas_pixmap)
        painter.setPen(QPen(self.current_color, self.current_pen_size, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

        painter.drawLine(self.last_draw_pos, current_pos)
        painter.end()

        self.drawing_canvas.setPixmap(self.canvas_pixmap)
        self.last_draw_pos = current_pos

    # --- Fruit Slicer 逻辑 ---

    def spawn_random_fruit(self):
        """随机生成水果。"""
        fruit_names = ["apple", "pear", "lemon"]
        name = random.choice(fruit_names)

        file_path = os.path.join(self.full_output_dir, f"{name}_full.png")
        pixmap = QPixmap(file_path)

        if pixmap.isNull(): return

        scaled_pixmap = pixmap.scaled(FRUIT_SIZE, FRUIT_SIZE, Qt.KeepAspectRatio)
        fruit_item = FruitItem(scaled_pixmap, name)

        x_start = random.randint(GAME_WIDTH // 4, GAME_WIDTH * 3 // 4)
        y_start = GAME_HEIGHT + FRUIT_SIZE
        fruit_item.setPos(x_start, y_start)

        vx = random.uniform(-6, 6)
        vy = random.uniform(-20, -30)
        fruit_item.velocity = QPointF(vx, vy)

        self.active_fruits.append(fruit_item)
        self.scene.addItem(fruit_item)

    def _update_fruits(self):
        """更新水果和碎片的位置，并进行切割检测。"""
        for item in list(self.active_fruits):
            item.velocity += QPointF(0, GRAVITY * item.gravity_factor)
            item.setPos(item.pos() + item.velocity)

            if item.pos().y() > GAME_HEIGHT + FRUIT_SIZE * 2:
                self.scene.removeItem(item)
                self.active_fruits.remove(item)
                continue

            if not item.is_sliced and self.is_grasping:  # 只有握拳时才视为刀
                self.check_for_cuts(item)

    def check_for_cuts(self, fruit_to_check):
        """检测手部最近的线段是否与水果矩形相交。"""
        if len(self.knife_trail) < 2:
            return

        p1 = self.knife_trail[-2]
        p2 = self.knife_trail[-1]

        fruit_rect = fruit_to_check.sceneBoundingRect()

        if fruit_rect.contains(p1) or fruit_rect.contains(p2):
            self.slice_fruit(fruit_to_check)

    def slice_fruit(self, fruit_to_slice):
        """执行切割逻辑：移除完整水果，生成碎片。"""

        self.scene.removeItem(fruit_to_slice)
        self.active_fruits.remove(fruit_to_slice)

        name = fruit_to_slice.original_name
        slice_parts = self.slice_parts.get(name, [])
        initial_pos = fruit_to_slice.pos()
        initial_vel = fruit_to_slice.velocity

        for part in slice_parts:
            slice_path = os.path.join(self.slices_output_dir, f"{name}_slice_{part}.png")
            slice_pixmap = QPixmap(slice_path)

            if slice_pixmap.isNull(): continue

            scaled_pixmap = slice_pixmap.scaled(FRUIT_SIZE, FRUIT_SIZE, Qt.KeepAspectRatio)
            slice_item = SliceItem(scaled_pixmap, f"{name}_{part}")

            slice_item.setPos(initial_pos)

            # 赋予碎片分离速度
            vx_offset = random.uniform(5, 15) * (1 if 'right' in part or 'half2' in part else -1)
            vy_offset = random.uniform(5, 15) * (-1 if 'top' in part or 'left' in part else 1)

            slice_item.velocity = QPointF(initial_vel.x() + vx_offset,
                                          initial_vel.y() + initial_vel.y() / 2 - vy_offset)

            self.scene.addItem(slice_item)
            self.active_fruits.append(slice_item)

        print(f"--- 成功切割 {name}！ ---")

    # --- 线程通信和清理 ---

    def update_knife_position(self, x_norm, y_norm):
        """接收平滑坐标，更新光标的位置和轨迹。"""

        view_width = self.game_view.viewport().width()
        view_height = self.game_view.viewport().height()

        # 调整坐标映射比例
        x_pixel = x_norm * view_width * 1.5
        y_pixel = y_norm * view_height * 1.5

        x_pixel = max(0, min(x_pixel, GAME_WIDTH))
        y_pixel = max(0, min(y_pixel, GAME_HEIGHT))

        self.cursor_pos = QPointF(x_pixel, y_pixel)

        # 1. 更新光标图形位置
        cursor_radius = self.knife_cursor.rect().width() / 2
        self.knife_cursor.setPos(x_pixel - cursor_radius, y_pixel - cursor_radius)

        # 2. 记录轨迹
        if self.game_state == 'drawing_app' or self.game_state == 'fruit_slicer':
            self.knife_trail.append(QPointF(x_pixel, y_pixel))

        # 3. 更新坐标文本
        self.coord_display.setText(f"X: {x_norm:.2f}, Y: {y_norm:.2f}")

    def update_video(self, pixmap):
        """接收 Worker 线程的视频流，显示在左上角。"""
        if pixmap.isNull(): return
        scaled_pixmap = pixmap.scaled(
            self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.video_label.setPixmap(scaled_pixmap)

    def closeEvent(self, event):
        """安全停止工作线程和定时器。"""
        print("--- 正在安全关闭程序... ---")
        self.game_timer.stop()
        self.spawn_timer.stop()
        if self.worker.isRunning():
            self.worker.stop()
        super().closeEvent(event)
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_EnableHighDpiScaling)

    try:
        window = GameWindow()
        window.show()
        sys.exit(app.exec_())
    except Exception as e:
        print("\n--------------------------------------------------")
        print(f"致命错误：主程序启动阶段崩溃！错误信息：{e}")
        import traceback

        traceback.print_exc()
        print("--------------------------------------------------\n")