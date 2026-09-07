from __future__ import annotations

import logging
from pathlib import Path
import os

from PySide6.QtCore import QFileSystemWatcher, QPointF, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDropEvent,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QDialog,
    QDialogButtonBox,
    QGraphicsScene,
    QGraphicsView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
)

from app.filesystem.operations import FilesystemError, FilesystemService
from app.git.repository import GitRepositoryError, GitRepositoryService, RepositoryInfo
from app.git.workflow_helper import GitWorkflowHelper
from app.remote.auth import AuthenticationError, GitHubAuthenticator, MacOSKeychainCredentialStore, RemoteAccount
from app.remote.provider import GitHubProvider, RemoteProviderError


logger = logging.getLogger(__name__)


class FileTreeWidget(QTreeWidget):
    drop_requested = Signal(object, object)

    def dropEvent(self, event: QDropEvent) -> None:
        target_item = self.itemAt(event.position().toPoint())
        target = target_item.data(0, Qt.ItemDataRole.UserRole) if target_item else None
        selected = [item.data(0, Qt.ItemDataRole.UserRole) for item in self.selectedItems()]
        sources = [path for path in selected if isinstance(path, Path)]
        if isinstance(target, Path):
            if not target.is_dir():
                event.ignore()
                return
            destination = target
            self.drop_requested.emit(sources, destination)
            event.accept()
        else:
            event.ignore()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.filesystem = FilesystemService()
        self.git = GitRepositoryService()
        self.workflow_helper = GitWorkflowHelper()
        self.repository: RepositoryInfo | None = None
        self.current_directory: Path | None = None
        self.clipboard_paths: list[Path] = []
        self.clipboard_mode: str | None = None
        self.github_account: RemoteAccount | None = None
        self._icon_buttons: dict[QPushButton, tuple[str, bool]] = {}
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self._schedule_reconcile)
        self.watcher.fileChanged.connect(self._schedule_reconcile)
        self.reconcile_timer = QTimer(self)
        self.reconcile_timer.setSingleShot(True)
        self.reconcile_timer.setInterval(150)
        self.reconcile_timer.timeout.connect(self.refresh)
        self.reconcile_poll_timer = QTimer(self)
        self.reconcile_poll_timer.setInterval(500)
        self.reconcile_poll_timer.timeout.connect(self._reconcile_external_changes)
        self.setWindowTitle("Visual Git Workspace")
        self.resize(1280, 800)
        self.setMinimumSize(QSize(980, 620))
        self._build_ui()
        self._apply_theme("Light")
        self._set_empty_state()

    @staticmethod
    def _elevate(widget: QWidget, blur: int = 24, alpha: int = 40, y_offset: int = 4) -> None:
        """Attach a soft, professional drop shadow to a card-style panel."""
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur)
        effect.setOffset(0, y_offset)
        effect.setColor(QColor(15, 23, 42, alpha))
        widget.setGraphicsEffect(effect)

    # -------------------------------------------------------------------
    # Hand-drawn vector icon set. Nothing here depends on external icon
    # files, so the toolbar always renders crisply and can be recolored
    # instantly for light/dark themes.
    # -------------------------------------------------------------------
    def _vector_icon(self, kind: str, color: QColor, size: int = 20) -> QIcon:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = size / 24.0
        painter.scale(scale, scale)
        pen = QPen(color)
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        def arrowhead(tip: QPointF, dx: float, dy: float, spread: float = 3.2) -> None:
            # Small filled triangle pointing along (dx, dy) with apex at tip.
            length = (dx ** 2 + dy ** 2) ** 0.5 or 1.0
            ux, uy = dx / length, dy / length
            px, py = -uy, ux
            base = QPointF(tip.x() - ux * 5.2, tip.y() - uy * 5.2)
            left = QPointF(base.x() + px * spread, base.y() + py * spread)
            right = QPointF(base.x() - px * spread, base.y() - py * spread)
            path = QPainterPath()
            path.moveTo(tip)
            path.lineTo(left)
            path.lineTo(right)
            path.closeSubpath()
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(pen)

        if kind == "new_folder":
            path = QPainterPath()
            path.moveTo(3, 6.5)
            path.lineTo(9.5, 6.5)
            path.lineTo(11.5, 8.5)
            path.lineTo(21, 8.5)
            path.lineTo(21, 18.5)
            path.lineTo(3, 18.5)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(QPointF(12, 11), QPointF(12, 16))
            painter.drawLine(QPointF(9.5, 13.5), QPointF(14.5, 13.5))
        elif kind == "open_folder":
            path = QPainterPath()
            path.moveTo(3, 6.5)
            path.lineTo(9.5, 6.5)
            path.lineTo(11.5, 8.5)
            path.lineTo(21, 8.5)
            path.lineTo(21, 18.5)
            path.lineTo(3, 18.5)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(QPointF(9.5, 13.5), QPointF(14.5, 13.5))
            arrowhead(QPointF(14.5, 13.5), 1, 0)
        elif kind == "copy":
            painter.drawRoundedRect(QRectF(4, 3, 12, 14), 2, 2)
            painter.setBrush(QBrush(color.lighter(180) if color.lightness() < 128 else QColor(255, 255, 255)))
            painter.drawRoundedRect(QRectF(8.5, 8, 12, 13), 2, 2)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        elif kind == "cut":
            painter.drawEllipse(QRectF(3.5, 15.5, 4, 4))
            painter.drawEllipse(QRectF(16.5, 15.5, 4, 4))
            painter.drawLine(QPointF(6.5, 16.5), QPointF(19, 5))
            painter.drawLine(QPointF(18.5, 16.5), QPointF(6, 5))
        elif kind == "paste":
            painter.drawRoundedRect(QRectF(5, 5, 14, 16), 2, 2)
            painter.drawRoundedRect(QRectF(9, 3, 6, 3.5), 1.2, 1.2)
            painter.drawLine(QPointF(8, 12), QPointF(16, 12))
            painter.drawLine(QPointF(8, 15.5), QPointF(16, 15.5))
        elif kind == "rename":
            path = QPainterPath()
            path.moveTo(4.5, 19.5)
            path.lineTo(5.3, 15.7)
            path.lineTo(14.7, 6.3)
            path.lineTo(17.7, 9.3)
            path.lineTo(8.3, 18.7)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(QPointF(13.2, 7.8), QPointF(16.2, 10.8))
            painter.drawLine(QPointF(4.5, 19.5), QPointF(8.3, 18.7))
        elif kind == "delete":
            painter.drawLine(QPointF(5, 8), QPointF(19, 8))
            painter.drawLine(QPointF(9.5, 5), QPointF(14.5, 5))
            painter.drawRoundedRect(QRectF(7, 8, 10, 12), 1.5, 1.5)
            painter.drawLine(QPointF(10, 11), QPointF(10, 17))
            painter.drawLine(QPointF(14, 11), QPointF(14, 17))
        elif kind == "stage_selected":
            painter.drawLine(QPointF(12, 17), QPointF(12, 6.5))
            arrowhead(QPointF(12, 5.5), 0, -1)
            painter.drawLine(QPointF(6, 19.5), QPointF(18, 19.5))
        elif kind == "stage_all":
            painter.drawLine(QPointF(9, 17), QPointF(9, 6.5))
            arrowhead(QPointF(9, 5.5), 0, -1, spread=2.6)
            painter.drawLine(QPointF(15, 17), QPointF(15, 6.5))
            arrowhead(QPointF(15, 5.5), 0, -1, spread=2.6)
            painter.drawLine(QPointF(5.5, 19.5), QPointF(18.5, 19.5))
        elif kind == "unstage":
            painter.drawLine(QPointF(12, 7), QPointF(12, 17.5))
            arrowhead(QPointF(12, 18.5), 0, 1)
            painter.drawLine(QPointF(6, 4.5), QPointF(18, 4.5))
        elif kind == "commit":
            painter.drawLine(QPointF(2.5, 12), QPointF(8, 12))
            painter.drawLine(QPointF(16, 12), QPointF(21.5, 12))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QRectF(8, 8, 8, 8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        elif kind == "history":
            painter.drawEllipse(QRectF(3.5, 3.5, 17, 17))
            painter.drawLine(QPointF(12, 7.5), QPointF(12, 12.2))
            painter.drawLine(QPointF(12, 12.2), QPointF(15.5, 14.5))
        elif kind == "branches":
            painter.drawEllipse(QRectF(5, 3, 4, 4))
            painter.drawEllipse(QRectF(5, 17, 4, 4))
            painter.drawEllipse(QRectF(15, 10, 4, 4))
            painter.drawLine(QPointF(7, 7), QPointF(7, 17))
            path = QPainterPath()
            path.moveTo(7, 11)
            path.cubicTo(7, 13.5, 12, 13.5, 17, 12.2)
            painter.drawPath(path)
        elif kind == "graph":
            painter.drawEllipse(QRectF(3.5, 15, 4, 4))
            painter.drawEllipse(QRectF(10, 9, 4, 4))
            painter.drawEllipse(QRectF(16.5, 3.5, 4, 4))
            painter.drawLine(QPointF(7, 15.5), QPointF(11.5, 12.5))
            painter.drawLine(QPointF(13.5, 9.5), QPointF(17.5, 7))
        elif kind == "merge":
            painter.drawEllipse(QRectF(4, 3, 4, 4))
            painter.drawEllipse(QRectF(16, 3, 4, 4))
            painter.drawEllipse(QRectF(10, 17, 4, 4))
            path_left = QPainterPath()
            path_left.moveTo(6, 7)
            path_left.cubicTo(6, 13, 10, 12, 12, 17)
            painter.drawPath(path_left)
            path_right = QPainterPath()
            path_right.moveTo(18, 7)
            path_right.cubicTo(18, 13, 14, 12, 12, 17)
            painter.drawPath(path_right)
        elif kind == "conflicts":
            path = QPainterPath()
            path.moveTo(12, 3.5)
            path.lineTo(21, 20)
            path.lineTo(3, 20)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(QPointF(12, 10), QPointF(12, 14.5))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QRectF(11.1, 16, 1.8, 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        elif kind == "up":
            painter.drawLine(QPointF(12, 18), QPointF(12, 7))
            painter.drawLine(QPointF(6.5, 12.5), QPointF(12, 7))
            painter.drawLine(QPointF(17.5, 12.5), QPointF(12, 7))
        elif kind == "refresh":
            painter.drawArc(QRectF(4, 4, 16, 16), 40 * 16, 260 * 16)
            arrowhead(QPointF(18.6, 6.1), 0.6, -0.9, spread=2.8)
        else:
            painter.drawEllipse(QRectF(5, 5, 14, 14))

        painter.end()
        return QIcon(pixmap)


def _toolbar_button(self, kind: str, label: str, handler, tooltip: str, primary: bool = False) -> QPushButton:
    """Create a polished, icon-first toolbar control."""
    button = QPushButton()
    button.setFixedSize(44, 44)
    button.setIconSize(QSize(22, 22))
    button.setToolTip(f"<b>{label}</b><br/>{tooltip}")
    button.setAccessibleName(label)
    button.clicked.connect(handler)
    button.setProperty("toolIcon", True)
    button.setProperty("primaryAction", primary)
    if primary:
        button.setObjectName("primaryButton")
    self._icon_buttons[button] = (kind, primary)
    return button

    def closeEvent(self, event) -> None:
        self.reconcile_timer.stop()
        self.reconcile_poll_timer.stop()
        super().closeEvent(event)


def _build_ui(self) -> None:
    central_widget = QWidget()
    central_widget.setObjectName("root")
    outer_layout = QVBoxLayout(central_widget)
    outer_layout.setContentsMargins(0, 0, 0, 0)
    outer_layout.setSpacing(0)

    # ---------------------------------------------------------------
    # Professional application chrome
    # ---------------------------------------------------------------
    app_bar = QFrame()
    app_bar.setObjectName("appBar")
    app_bar_layout = QHBoxLayout(app_bar)
    app_bar_layout.setContentsMargins(22, 14, 22, 14)
    app_bar_layout.setSpacing(10)

    brand_icon = QLabel()
    brand_icon.setObjectName("brandIcon")
    brand_icon.setFixedSize(30, 30)
    app_bar_layout.addWidget(brand_icon)

    brand_title = QLabel("Visual Git")
    brand_title.setObjectName("brandTitle")
    app_bar_layout.addWidget(brand_title)

    brand_subtitle = QLabel("LOCAL GIT WORKSPACE")
    brand_subtitle.setObjectName("brandSubtitle")
    app_bar_layout.addWidget(brand_subtitle)
    app_bar_layout.addSpacing(14)

    open_button = QPushButton("Open Repository")
    open_button.setObjectName("openRepositoryButton")
    open_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
    open_button.setIconSize(QSize(19, 19))
    open_button.setFixedHeight(40)
    open_button.clicked.connect(self.open_repository)
    open_button.setToolTip("Open an existing local Git repository")
    app_bar_layout.addWidget(open_button)

    self.up_button = self._toolbar_button("up", "Up", self.go_up, "Go to the parent folder")
    app_bar_layout.addWidget(self.up_button)

    refresh_button = self._toolbar_button("refresh", "Refresh", self.refresh, "Refresh files and local Git status")
    app_bar_layout.addWidget(refresh_button)

    app_bar_layout.addStretch()

    appearance_label = QLabel("Appearance")
    appearance_label.setObjectName("mutedLabel")
    app_bar_layout.addWidget(appearance_label)

    self.theme_selector = QComboBox()
    self.theme_selector.addItems(["Light", "Dark"])
    self.theme_selector.currentTextChanged.connect(self._apply_theme)
    self.theme_selector.setToolTip("Switch between Light and Dark appearance")
    self.theme_selector.setFixedSize(112, 38)
    app_bar_layout.addWidget(self.theme_selector)
    outer_layout.addWidget(app_bar)

    # ---------------------------------------------------------------
    # Main canvas
    # ---------------------------------------------------------------
    body = QWidget()
    body.setObjectName("canvas")
    layout = QVBoxLayout(body)
    layout.setContentsMargins(22, 18, 22, 18)
    layout.setSpacing(12)
    outer_layout.addWidget(body, 1)

    # Repository context
    context_bar = QFrame()
    context_bar.setObjectName("contextBar")
    context_layout = QHBoxLayout(context_bar)
    context_layout.setContentsMargins(16, 11, 16, 11)
    context_layout.setSpacing(10)

    repo_icon = QLabel()
    repo_icon.setObjectName("contextIcon")
    repo_icon.setFixedSize(28, 28)
    context_layout.addWidget(repo_icon)

    context_text = QVBoxLayout()
    context_text.setContentsMargins(0, 0, 0, 0)
    context_text.setSpacing(1)

    self.repository_label = QLabel()
    self.repository_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    self.repository_label.setObjectName("repositorySummary")
    context_text.addWidget(self.repository_label)

    self.repository_path_label = QLabel()
    self.repository_path_label.setObjectName("repositoryPath")
    self.repository_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    context_text.addWidget(self.repository_path_label)
    context_layout.addLayout(context_text, 1)

    self.branch_badge = QLabel("No branch")
    self.branch_badge.setObjectName("branchBadge")
    context_layout.addWidget(self.branch_badge)
    layout.addWidget(context_bar)

    # ---------------------------------------------------------------
    # Action rail
    # ---------------------------------------------------------------
    actions_card = QFrame()
    actions_card.setObjectName("toolRail")
    actions_outer = QHBoxLayout(actions_card)
    actions_outer.setContentsMargins(12, 9, 12, 9)
    actions_outer.setSpacing(7)

    filesystem_label = QLabel("FILES")
    filesystem_label.setObjectName("sectionTag")
    actions_outer.addWidget(filesystem_label)

    for kind, label, handler, tooltip in (
        ("new_folder", "New Folder", self.create_folder, "Create a folder in the current location"),
        ("copy", "Copy", self.copy_selected, "Copy selected files or folders"),
        ("cut", "Cut", self.cut_selected, "Move selected files or folders"),
        ("paste", "Paste", self.paste, "Paste into the selected folder"),
        ("rename", "Rename", self.rename_selected, "Rename the selected file or folder"),
        ("delete", "Delete", self.delete_selected, "Delete selected files or folders"),
        ("open_folder", "Open", self.open_selected, "Open a file or enter a folder"),
    ):
        actions_outer.addWidget(self._toolbar_button(kind, label, handler, tooltip))

    divider = QFrame()
    divider.setObjectName("verticalDivider")
    divider.setFrameShape(QFrame.Shape.VLine)
    divider.setFixedHeight(28)
    actions_outer.addWidget(divider)

    git_label = QLabel("GIT")
    git_label.setObjectName("sectionTag")
    actions_outer.addWidget(git_label)

    for kind, label, handler, tooltip, emphasis in (
        ("stage_selected", "Stage Selected", self.stage_selected, "Stage selected changes", False),
        ("stage_all", "Stage All", self.stage_all, "Stage all local changes", False),
        ("unstage", "Unstage", self.unstage_selected, "Unstage selected changes", False),
        ("commit", "Commit", self.commit_changes, "Create a local commit", True),
        ("history", "History", self.show_history, "Inspect local history", False),
        ("branches", "Branches", self.manage_branches, "Manage local branches", False),
        ("graph", "Graph", self.show_graph, "View the local commit graph", False),
        ("merge", "Merge", self.merge_branch, "Merge a local branch", False),
        ("conflicts", "Conflicts", self.show_conflicts, "Resolve an active merge conflict", False),
    ):
        actions_outer.addWidget(self._toolbar_button(kind, label, handler, tooltip, primary=emphasis))
    actions_outer.addStretch()
    layout.addWidget(actions_card)

    # Keep remote controls in the codebase, but do not expose them while
    # remote work is intentionally frozen.
    self.create_github_repository_button = QPushButton("Create GitHub Repository")
    self.create_github_repository_button.clicked.connect(self.create_github_repository)
    self.create_github_repository_button.hide()
    self.connect_github_button = QPushButton("Connect GitHub")
    self.connect_github_button.clicked.connect(self.connect_github)
    self.connect_github_button.hide()
    self.disconnect_github_button = QPushButton("Disconnect GitHub")
    self.disconnect_github_button.clicked.connect(self.disconnect_github)
    self.disconnect_github_button.hide()

    # ---------------------------------------------------------------
    # Workspace
    # ---------------------------------------------------------------
    workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
    workspace_splitter.setObjectName("workspaceSplitter")
    workspace_splitter.setChildrenCollapsible(False)
    workspace_splitter.setHandleWidth(7)

    sidebar = QFrame()
    sidebar.setObjectName("sidebarPanel")
    sidebar.setMinimumWidth(205)
    sidebar.setMaximumWidth(270)
    sidebar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    sidebar_layout = QVBoxLayout(sidebar)
    sidebar_layout.setContentsMargins(12, 14, 12, 12)
    sidebar_layout.setSpacing(7)

    locations_header = QLabel("LOCATIONS")
    locations_header.setObjectName("sectionTag")
    sidebar_layout.addWidget(locations_header)

    self.locations_list = QListWidget()
    self.locations_list.setObjectName("locationsList")
    self.locations_list.setFrameShape(QFrame.Shape.NoFrame)
    self.locations_list.itemActivated.connect(self._navigate_location)
    sidebar_layout.addWidget(self.locations_list, 1)
    workspace_splitter.addWidget(sidebar)

    content = QWidget()
    content.setObjectName("contentArea")
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(9)

    breadcrumb_bar = QFrame()
    breadcrumb_bar.setObjectName("breadcrumbBar")
    breadcrumb_layout = QHBoxLayout(breadcrumb_bar)
    breadcrumb_layout.setContentsMargins(10, 3, 10, 3)
    self.breadcrumb_widget = QWidget()
    self.breadcrumb_widget.setObjectName("breadcrumbWidget")
    self.breadcrumb_layout = QHBoxLayout(self.breadcrumb_widget)
    self.breadcrumb_layout.setContentsMargins(0, 0, 0, 0)
    self.breadcrumb_layout.setSpacing(2)
    breadcrumb_layout.addWidget(self.breadcrumb_widget)
    content_layout.addWidget(breadcrumb_bar)

    self.tree = FileTreeWidget()
    self.tree.setObjectName("explorerTree")
    self.tree.setFrameShape(QFrame.Shape.NoFrame)
    self.tree.setHeaderLabels(["Name", "Status", "Type"])
    self.tree.setAlternatingRowColors(False)
    self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    self.tree.setSortingEnabled(True)
    self.tree.setUniformRowHeights(True)
    self.tree.setIndentation(20)
    self.tree.setIconSize(QSize(20, 20))
    self.tree.setAnimated(True)
    header = self.tree.header()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setMinimumSectionSize(80)
    header.setDefaultSectionSize(100)
    self.tree.setDragEnabled(True)
    self.tree.setAcceptDrops(True)
    self.tree.setDropIndicatorShown(True)
    self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    self.tree.itemDoubleClicked.connect(self.handle_double_click)
    self.tree.itemSelectionChanged.connect(self.update_selection)
    self.tree.drop_requested.connect(self.move_dropped)
    self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    self.tree.customContextMenuRequested.connect(self.show_context_menu)

    tree_frame = QFrame()
    tree_frame.setObjectName("explorerFrame")
    tree_layout = QVBoxLayout(tree_frame)
    tree_layout.setContentsMargins(0, 0, 0, 0)
    tree_layout.addWidget(self.tree)

    workflow_widget = QFrame()
    workflow_widget.setObjectName("workflowHelper")
    workflow_layout = QVBoxLayout(workflow_widget)
    workflow_layout.setContentsMargins(16, 13, 16, 13)
    workflow_layout.setSpacing(4)

    workflow_heading_row = QHBoxLayout()
    self.workflow_happening_label = QLabel("WHAT'S HAPPENING")
    self.workflow_happening_label.setObjectName("sectionTag")
    workflow_heading_row.addWidget(self.workflow_happening_label)
    workflow_heading_row.addStretch()
    workflow_layout.addLayout(workflow_heading_row)

    self.workflow_message_label = QLabel()
    self.workflow_message_label.setObjectName("workflowMessage")
    self.workflow_message_label.setWordWrap(True)
    workflow_layout.addWidget(self.workflow_message_label)

    self.workflow_next_label = QLabel("NEXT STEP")
    self.workflow_next_label.setObjectName("sectionTag")
    workflow_layout.addWidget(self.workflow_next_label)

    self.workflow_step_label = QLabel()
    self.workflow_step_label.setObjectName("workflowStep")
    self.workflow_step_label.setWordWrap(True)
    workflow_layout.addWidget(self.workflow_step_label)

    self.workflow_action_button = QPushButton()
    self.workflow_action_button.setObjectName("primaryButton")
    self.workflow_action_button.setMinimumHeight(36)
    self.workflow_action_button.clicked.connect(self._run_workflow_action)
    workflow_layout.addWidget(self.workflow_action_button)

    changes_group = QFrame()
    changes_group.setObjectName("changesPanel")
    changes_layout = QVBoxLayout(changes_group)
    changes_layout.setContentsMargins(14, 13, 14, 12)
    changes_layout.setSpacing(7)

    changes_heading = QHBoxLayout()
    changes_header = QLabel("CHANGES")
    changes_header.setObjectName("sectionTag")
    changes_heading.addWidget(changes_header)
    self.changes_count_label = QLabel("0")
    self.changes_count_label.setObjectName("countBadge")
    changes_heading.addWidget(self.changes_count_label)
    changes_heading.addStretch()
    changes_layout.addLayout(changes_heading)

    self.changes_list = QListWidget()
    self.changes_list.setObjectName("changesList")
    self.changes_list.setFrameShape(QFrame.Shape.NoFrame)
    self.changes_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    changes_layout.addWidget(self.changes_list)

    explorer_splitter = QSplitter(Qt.Orientation.Vertical)
    explorer_splitter.setObjectName("explorerSplitter")
    explorer_splitter.setChildrenCollapsible(False)
    explorer_splitter.setHandleWidth(7)
    explorer_splitter.addWidget(tree_frame)

    bottom_panel = QWidget()
    bottom_layout = QHBoxLayout(bottom_panel)
    bottom_layout.setContentsMargins(0, 0, 0, 0)
    bottom_layout.setSpacing(9)
    bottom_layout.addWidget(workflow_widget, 2)
    bottom_layout.addWidget(changes_group, 1)

    explorer_splitter.addWidget(bottom_panel)
    explorer_splitter.setStretchFactor(0, 4)
    explorer_splitter.setStretchFactor(1, 1)
    explorer_splitter.setSizes([500, 190])

    content_layout.addWidget(explorer_splitter, 1)
    workspace_splitter.addWidget(content)
    workspace_splitter.setStretchFactor(0, 0)
    workspace_splitter.setStretchFactor(1, 1)
    workspace_splitter.setSizes([220, 980])
    layout.addWidget(workspace_splitter, 1)

    self.setCentralWidget(central_widget)

    # Menus remain available for keyboard-oriented workflows.
    file_menu = self.menuBar().addMenu("File")
    open_action = QAction("Open Repository", self)
    open_action.triggered.connect(self.open_repository)
    file_menu.addAction(open_action)

    edit_menu = self.menuBar().addMenu("Edit")
    for action in self._edit_actions():
        edit_menu.addAction(action)

    view_menu = self.menuBar().addMenu("View")
    theme_light = QAction("Light Appearance", self)
    theme_dark = QAction("Dark Appearance", self)
    theme_light.triggered.connect(lambda: self.theme_selector.setCurrentText("Light"))
    theme_dark.triggered.connect(lambda: self.theme_selector.setCurrentText("Dark"))
    view_menu.addActions([theme_light, theme_dark])

    def _edit_actions(self, parent=None) -> list[QAction]:
        actions = []
        for label, shortcut, handler in (
            ("Copy", QKeySequence.StandardKey.Copy, self.copy_selected),
            ("Cut", QKeySequence.StandardKey.Cut, self.cut_selected),
            ("Paste", QKeySequence.StandardKey.Paste, self.paste),
            ("Rename", QKeySequence("F2"), self.rename_selected),
            ("Delete", QKeySequence.StandardKey.Delete, self.delete_selected),
            ("Open", QKeySequence(Qt.Key.Key_Return), self.open_selected),
        ):
            action = QAction(label, parent or self)
            if parent is None:
                if label == "Delete":
                    action.setShortcuts([shortcut, QKeySequence(Qt.Key.Key_Backspace)])
                else:
                    action.setShortcut(shortcut)
            action.triggered.connect(handler)
            actions.append(action)
        return actions


def _set_empty_state(self) -> None:
    self.repository_label.setText("No repository open")
    self.repository_path_label.setText("Open a local Git repository to begin")
    self.branch_badge.setText("No branch")
    self.up_button.setEnabled(False)
    self.tree.clear()
    self.changes_list.clear()
    self.changes_count_label.setText("0")
    self.workflow_message_label.setText("Open a local Git repository to get started.")
    self.workflow_step_label.setText("Choose a repository from the toolbar.")
    self.workflow_action_button.hide()

    def open_repository(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open Local Repository")
        if directory:
            self.load_directory(Path(directory))

    def load_directory(self, directory: Path) -> None:
        try:
            repository = self.git.detect(directory)
        except GitRepositoryError as error:
            self.show_error(str(error))
            return
        if repository is None:
            QMessageBox.information(
                self,
                "Not a Git repository",
                "Choose a directory inside a Git repository. A repository must be initialized separately.",
            )
            return
        self.repository = repository
        self.current_directory = directory.resolve()
        self.reconcile_poll_timer.start()
        self._configure_watcher()
        self.refresh()

    def refresh(self) -> None:
        if self.repository is None or self.current_directory is None:
            self._set_empty_state()
            return
        try:
            current_repository = self.git.detect(self.repository.root)
        except GitRepositoryError as error:
            self.show_error(str(error))
            return
        if current_repository is None:
            self.show_error("The open folder is no longer a Git repository.")
            return
        self.repository = current_repository
        expanded_paths, selected_paths, current_path, scroll_position = self._tree_ui_state()
        try:
            entries = self.filesystem.list_directory(self.current_directory)
        except FilesystemError as error:
            self.show_error(str(error))
            return

        self.tree.clear()
        statuses = self._status_map()
        root_item = QTreeWidgetItem([self.current_directory.name or str(self.current_directory), "Unchanged", "Folder"])
        root_item.setData(0, Qt.ItemDataRole.UserRole, self.current_directory)
        root_item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.tree.addTopLevelItem(root_item)
        self._populate_item(root_item, entries, statuses)
        root_item.setExpanded(True)
        self._restore_tree_ui_state(expanded_paths, selected_paths, current_path, scroll_position)
        self.repository_label.setText(self._repository_summary())
        self.up_button.setEnabled(self.current_directory != self.repository.root)
        self._update_changes_panel(statuses)
        self._update_workflow_helper(statuses)
        self._update_navigation()
        self._update_status_bar(statuses)
        self._configure_watcher()

    def _tree_ui_state(self) -> tuple[set[Path], set[Path], Path | None, int]:
        expanded: set[Path] = set()
        selected: set[Path] = set()
        current = self.tree.currentItem()
        current_path = current.data(0, Qt.ItemDataRole.UserRole) if current else None

        def visit(item: QTreeWidgetItem) -> None:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(path, Path):
                if item.isExpanded():
                    expanded.add(path)
                if item.isSelected():
                    selected.add(path)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
        return expanded, selected, current_path if isinstance(current_path, Path) else None, self.tree.verticalScrollBar().value()

    def _restore_tree_ui_state(
        self,
        expanded_paths: set[Path],
        selected_paths: set[Path],
        current_path: Path | None,
        scroll_position: int,
    ) -> None:
        items: dict[Path, QTreeWidgetItem] = {}

        def visit(item: QTreeWidgetItem) -> None:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(path, Path):
                items[path] = item
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
        for path in expanded_paths:
            if path in items:
                items[path].setExpanded(True)
        for path in selected_paths:
            if path in items:
                items[path].setSelected(True)
        if current_path in items:
            self.tree.setCurrentItem(items[current_path])
        self.tree.verticalScrollBar().setValue(scroll_position)


def _populate_item(self, parent_item: QTreeWidgetItem, entries: list[Path], statuses: dict[Path, object]) -> None:
    for entry in entries:
        kind = "Folder" if entry.is_dir() else "File"
        status = statuses.get(entry.relative_to(self.repository.root)) if self.repository else None
        status_text = status.symbol if status else ""
        child = QTreeWidgetItem([entry.name, status_text, kind])
        child.setData(0, Qt.ItemDataRole.UserRole, entry)
        child.setToolTip(0, str(entry))
        child.setToolTip(1, status.label if status else "Unchanged")
        child.setToolTip(2, kind)

        icon = QStyle.StandardPixmap.SP_DirIcon if entry.is_dir() else QStyle.StandardPixmap.SP_FileIcon
        child.setIcon(0, self.style().standardIcon(icon))

        if status is not None:
            child.setData(1, Qt.ItemDataRole.UserRole + 2, status.label)

        parent_item.addChild(child)
        if entry.is_dir() and not entry.is_symlink():
            try:
                self._populate_item(child, self.filesystem.list_directory(entry), statuses)
            except FilesystemError:
                child.setText(1, "!")
                child.setToolTip(1, "Folder is unreadable")

    def _status_map(self) -> dict[Path, object]:
        if self.repository is None:
            return {}
        try:
            return {status.path: status for status in self.git.status(self.repository)}
        except GitRepositoryError as error:
            self.show_error(str(error))
            return {}


def _update_changes_panel(self, statuses: dict[Path, object]) -> None:
    self.changes_list.clear()
    staged: list[tuple[str, Path, object]] = []
    working_tree: list[tuple[str, Path, object]] = []
    for relative_path, status in sorted(statuses.items(), key=lambda item: item[0].as_posix().casefold()):
        if status.index_code != " " and status.index_code != "?":
            staged.append((relative_path.as_posix(), relative_path, status))
        if status.worktree_code != " " or status.worktree_code == "?":
            working_tree.append((relative_path.as_posix(), relative_path, status))

    total = len({path for _, path, _ in staged + working_tree})
    self.changes_count_label.setText(str(total))

    def add_header(title: str, count: int) -> None:
        header = QListWidgetItem(f"{title.upper()}  ·  {count}")
        header.setData(Qt.ItemDataRole.UserRole, None)
        header.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.changes_list.addItem(header)

    add_header("Staged", len(staged))
    for text, relative_path, status in staged:
        self._add_change_item(f"{status.symbol}   {text}", relative_path, True)

    add_header("Working Tree", len(working_tree))
    for text, relative_path, status in working_tree:
        self._add_change_item(f"{status.symbol}   {text}", relative_path, False)

    def _update_workflow_helper(self, statuses: dict[Path, object]) -> None:
        if self.repository is None:
            self.workflow_message_label.setText("")
            self.workflow_step_label.setText("")
            self.workflow_action_button.hide()
            return
        try:
            conflict_state = self.git.conflict_state(self.repository)
        except GitRepositoryError as error:
            self.show_error(str(error))
            return
        guidance = self.workflow_helper.recommend(statuses.values(), conflict_state)
        self.workflow_message_label.setText(guidance.happening)
        self.workflow_step_label.setText(guidance.next_step)
        if guidance.action_label and guidance.action:
            self.workflow_action_button.setText(guidance.action_label)
            self.workflow_action_button.setProperty("workflow_action", guidance.action)
            self.workflow_action_button.show()
        else:
            self.workflow_action_button.hide()

    def _run_workflow_action(self) -> None:
        action = self.workflow_action_button.property("workflow_action")
        if action == "stage_all":
            self.stage_all()
        elif action in {"commit_changes", "commit_merge"}:
            self.commit_changes()
        elif action == "resolve_conflicts":
            self.show_conflicts()


def _add_change_item(self, text: str, relative_path: Path, staged: bool) -> None:
    item = QListWidgetItem(text)
    item.setData(Qt.ItemDataRole.UserRole, relative_path)
    item.setData(Qt.ItemDataRole.UserRole + 1, staged)
    item.setToolTip(f"{relative_path.as_posix()}\n{'Staged' if staged else 'Working tree change'}")
    self.changes_list.addItem(item)

    def _selected_change_paths(self, staged: bool) -> list[Path]:
        panel_paths = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.changes_list.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole + 1) is staged
        }
        if panel_paths:
            return sorted(path for path in panel_paths if isinstance(path, Path))
        if self.repository is None:
            return []
        return sorted(
            path.relative_to(self.repository.root)
            for path in self.selected_paths()
            if self._path_has_staged_state(path) is staged
        )

    def _path_has_staged_state(self, path: Path) -> bool:
        if self.repository is None:
            return False
        relative_path = path.relative_to(self.repository.root)
        return any(
            status.path == relative_path and status.index_code not in {" ", "?"}
            for status in self.git.status(self.repository)
        )

    def stage_selected(self) -> None:
        if self.repository is None:
            return
        try:
            paths = self._selected_change_paths(False)
            self.git.stage(self.repository, paths)
            self.refresh()
        except GitRepositoryError as error:
            self.show_error(str(error))

    def stage_all(self) -> None:
        if self.repository is None:
            return
        try:
            self.git.stage_all(self.repository)
            self.refresh()
        except GitRepositoryError as error:
            self.show_error(str(error))

    def unstage_selected(self) -> None:
        if self.repository is None:
            return
        try:
            paths = self._selected_change_paths(True)
            self.git.unstage(self.repository, paths)
            self.refresh()
        except GitRepositoryError as error:
            self.show_error(str(error))

    def commit_changes(self) -> None:
        if self.repository is None:
            return
        message, accepted = QInputDialog.getText(self, "Commit Changes", "Commit message:")
        if not accepted:
            return
        try:
            commit_hash = self.git.commit(self.repository, message)
            self.refresh()
            QMessageBox.information(self, "Commit created", f"Committed locally: {commit_hash[:12]}")
        except GitRepositoryError as error:
            self.show_error(str(error))

    def show_history(self) -> None:
        if self.repository is None:
            return
        selected = self.selected_path()
        try:
            if selected is not None and selected.is_file():
                relative_path = selected.relative_to(self.repository.root)
                commits = self.git.file_history(self.repository, relative_path)
                title = f"File History: {relative_path}"
            else:
                relative_path = None
                commits = self.git.history(self.repository)
                title = "Project History"
        except GitRepositoryError as error:
            self.show_error(str(error))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 520)
        layout = QVBoxLayout(dialog)
        entries = QListWidget()
        content = QTextEdit()
        content.setReadOnly(True)
        compare_bar = QHBoxLayout()
        compare_current_button = QPushButton("Compare With Current")
        compare_previous_button = QPushButton("Compare With Previous")
        copy_current_button = QPushButton("Copy This Version To Current File")
        branch_from_commit_button = QPushButton("Branch From This Commit")
        compare_bar.addWidget(compare_current_button)
        compare_bar.addWidget(compare_previous_button)
        compare_bar.addWidget(copy_current_button)
        compare_bar.addWidget(branch_from_commit_button)
        compare_bar.addStretch()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(entries)
        splitter.addWidget(content)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        layout.addLayout(compare_bar)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        for commit in commits:
            item = QListWidgetItem(f"{commit.short_hash}  {commit.timestamp[:10]}  {commit.subject}\n{commit.author}")
            item.setData(Qt.ItemDataRole.UserRole, commit.hash)
            entries.addItem(item)

        def inspect_commit(item: QListWidgetItem) -> None:
            if relative_path is None:
                content.setPlainText("Select a file to inspect historical contents.")
                return
            try:
                historical_content = self.git.show_file_at_commit(
                    self.repository,
                    item.data(Qt.ItemDataRole.UserRole),
                    relative_path,
                )
                content.setPlainText(historical_content)
            except GitRepositoryError as error:
                content.setPlainText(str(error))

        def compare_with_current() -> None:
            if relative_path is None or entries.currentItem() is None:
                return
            try:
                content.setPlainText(
                    self.git.diff_file_versions(
                        self.repository,
                        relative_path,
                        entries.currentItem().data(Qt.ItemDataRole.UserRole),
                    )
                )
            except GitRepositoryError as error:
                content.setPlainText(str(error))

        def compare_with_previous() -> None:
            current_row = entries.currentRow()
            if relative_path is None or current_row < 0 or current_row + 1 >= len(commits):
                return
            try:
                content.setPlainText(
                    self.git.diff_file_versions(
                        self.repository,
                        relative_path,
                        commits[current_row + 1].hash,
                        commits[current_row].hash,
                    )
                )
            except GitRepositoryError as error:
                content.setPlainText(str(error))

        def copy_to_current() -> None:
            if relative_path is None or entries.currentItem() is None:
                return
            answer = QMessageBox.question(
                dialog,
                "Copy historical content",
                "Copy this historical content into the current working file? This will modify the file on disk but will not rewrite Git history.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self.git.copy_file_from_commit(
                    self.repository,
                    entries.currentItem().data(Qt.ItemDataRole.UserRole),
                    relative_path,
                )
                self.refresh()
                content.setPlainText("Historical content copied to the current file. Git now reports the file as modified until you stage and commit it.")
            except GitRepositoryError as error:
                content.setPlainText(str(error))

        def branch_from_commit() -> None:
            if entries.currentItem() is None:
                return
            name, accepted = QInputDialog.getText(dialog, "Branch From Commit", "Branch name:")
            if not accepted:
                return
            try:
                self.git.create_branch(
                    self.repository,
                    name,
                    entries.currentItem().data(Qt.ItemDataRole.UserRole),
                )
                self.repository = self.git.detect(self.repository.root)
                self.refresh()
                dialog.accept()
            except GitRepositoryError as error:
                content.setPlainText(str(error))

        entries.currentItemChanged.connect(lambda current, _previous: inspect_commit(current) if current else None)
        compare_current_button.clicked.connect(compare_with_current)
        compare_previous_button.clicked.connect(compare_with_previous)
        copy_current_button.clicked.connect(copy_to_current)
        branch_from_commit_button.clicked.connect(branch_from_commit)
        compare_current_button.setEnabled(relative_path is not None)
        compare_previous_button.setEnabled(relative_path is not None and len(commits) > 1)
        copy_current_button.setEnabled(relative_path is not None)
        branch_from_commit_button.setEnabled(bool(commits))
        dialog.exec()

    def manage_branches(self) -> None:
        if self.repository is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Branches")
        dialog.resize(420, 360)
        layout = QVBoxLayout(dialog)
        branches_list = QListWidget()
        layout.addWidget(branches_list)
        action_bar = QHBoxLayout()
        for label, handler in (
            ("Create", lambda: self.create_branch(dialog, branches_list)),
            ("Switch", lambda: self.switch_branch(dialog, branches_list)),
            ("Rename", lambda: self.rename_branch(dialog, branches_list)),
            ("Delete", lambda: self.delete_branch(dialog, branches_list)),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            action_bar.addWidget(button)
        layout.addLayout(action_bar)
        close_button = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button.rejected.connect(dialog.reject)
        layout.addWidget(close_button)

        def populate() -> None:
            branches_list.clear()
            try:
                for branch in self.git.branches(self.repository):
                    item = QListWidgetItem(("* " if branch.current else "  ") + branch.name)
                    item.setData(Qt.ItemDataRole.UserRole, branch.name)
                    branches_list.addItem(item)
            except GitRepositoryError as error:
                self.show_error(str(error))

        populate()
        dialog.exec()

    def show_graph(self) -> None:
        if self.repository is None:
            return
        try:
            commits = self.git.graph_history(self.repository)
            branch_tips = self.git.branch_tips(self.repository)
        except GitRepositoryError as error:
            self.show_error(str(error))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Visual Git Graph")
        dialog.resize(900, 650)
        layout = QVBoxLayout(dialog)
        scene = QGraphicsScene(dialog)
        view = QGraphicsView(scene)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        layout.addWidget(view)
        close_button = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button.rejected.connect(dialog.reject)
        layout.addWidget(close_button)

        node_x = 80
        row_height = 72
        node_by_hash = {}
        for row, commit in enumerate(commits):
            y = 50 + row * row_height
            node_by_hash[commit.hash] = (node_x, y)
            if row:
                previous_x, previous_y = node_by_hash[commits[row - 1].hash]
                scene.addLine(node_x, previous_y, node_x, y, QPen(QBrush(Qt.GlobalColor.darkGray), 2))
            scene.addEllipse(node_x - 8, y - 8, 16, 16, QPen(Qt.GlobalColor.darkBlue), QBrush(QColor("#9ec5fe")))
            commit_text = scene.addText(f"{commit.short_hash}  {commit.subject}")
            commit_text.setPos(node_x + 24, y - 18)
            metadata_text = scene.addText(f"{commit.author}  {commit.timestamp[:19]}")
            metadata_text.setPos(node_x + 24, y + 4)

        for branch_name, commit_hash in branch_tips.items():
            position = node_by_hash.get(commit_hash)
            if position is not None:
                branch_text = scene.addText(f"[{branch_name}]")
                branch_text.setPos(10, position[1] - 12)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-20, -20, 20, 20))
        dialog.exec()

    def merge_branch(self) -> None:
        if self.repository is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Merge Branch")
        dialog.resize(420, 260)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Current branch: {self.repository.branch or 'detached HEAD'}"))
        branches_list = QListWidget()
        try:
            for branch in self.git.branches(self.repository):
                if not branch.current:
                    item = QListWidgetItem(branch.name)
                    item.setData(Qt.ItemDataRole.UserRole, branch.name)
                    branches_list.addItem(item)
        except GitRepositoryError as error:
            self.show_error(str(error))
            return
        layout.addWidget(QLabel("Merge branch:"))
        layout.addWidget(branches_list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        merge_button = buttons.addButton("Merge", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(dialog.reject)
        merge_button.clicked.connect(lambda: self._perform_merge(dialog, branches_list))
        layout.addWidget(buttons)
        dialog.exec()

    def _perform_merge(self, dialog: QDialog, branches_list: QListWidget) -> None:
        if self.repository is None or branches_list.currentItem() is None:
            return
        branch = branches_list.currentItem().data(Qt.ItemDataRole.UserRole)
        if not isinstance(branch, str):
            return
        try:
            result = self.git.merge(self.repository, branch)
            self.refresh()
            if result.succeeded:
                QMessageBox.information(dialog, "Merge completed", result.message)
                dialog.accept()
            else:
                conflicts = "\n".join(path.as_posix() for path in result.conflicts)
                QMessageBox.warning(
                    dialog,
                    "Merge conflicts",
                    f"Resolve these files before completing the merge:\n{conflicts}",
                )
        except GitRepositoryError as error:
            self.show_error(str(error))

    def show_conflicts(self) -> None:
        if self.repository is None:
            return
        try:
            state = self.git.conflict_state(self.repository)
        except GitRepositoryError as error:
            self.show_error(str(error))
            return
        if not state.in_progress or not state.conflicts:
            self.show_error("There is no active merge conflict.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Merge Conflicts")
        dialog.resize(560, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Resolve each file explicitly. No side is selected automatically."))
        conflicts_list = QListWidget()
        for path in state.conflicts:
            item = QListWidgetItem(path.as_posix())
            item.setData(Qt.ItemDataRole.UserRole, path)
            conflicts_list.addItem(item)
        layout.addWidget(conflicts_list)
        action_bar = QHBoxLayout()
        for label, handler in (
            ("Use Current", lambda: self._resolve_conflict_side(dialog, conflicts_list, True)),
            ("Use Incoming", lambda: self._resolve_conflict_side(dialog, conflicts_list, False)),
            ("Open Conflict Editor", lambda: self._open_conflict_editor(conflicts_list)),
            ("Mark Resolved", lambda: self._mark_conflict_resolved(dialog, conflicts_list)),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            action_bar.addWidget(button)
        layout.addLayout(action_bar)
        close_button = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button.rejected.connect(dialog.reject)
        layout.addWidget(close_button)
        dialog.exec()

    def manage_remotes(self) -> None:
        if self.repository is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Git Remotes")
        dialog.resize(620, 360)
        layout = QVBoxLayout(dialog)
        remotes_list = QListWidget()
        layout.addWidget(remotes_list)
        action_bar = QHBoxLayout()
        add_button = QPushButton("Add Remote")
        remove_button = QPushButton("Remove Remote")
        action_bar.addWidget(add_button)
        action_bar.addWidget(remove_button)
        layout.addLayout(action_bar)
        close_button = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button.rejected.connect(dialog.reject)
        layout.addWidget(close_button)

        def populate() -> None:
            remotes_list.clear()
            try:
                for remote in self.git.remotes(self.repository):
                    item = QListWidgetItem(f"{remote.name}\nFetch: {remote.fetch_url}\nPush: {remote.push_url}")
                    item.setData(Qt.ItemDataRole.UserRole, remote.name)
                    remotes_list.addItem(item)
            except GitRepositoryError as error:
                self.show_error(str(error))

        def add_remote() -> None:
            name, accepted = QInputDialog.getText(dialog, "Add Remote", "Remote name:")
            if not accepted:
                return
            url, accepted = QInputDialog.getText(dialog, "Add Remote", "Remote URL:")
            if not accepted:
                return
            try:
                self.git.add_remote(self.repository, name, url)
                populate()
            except GitRepositoryError as error:
                self.show_error(str(error))

        def remove_remote() -> None:
            item = remotes_list.currentItem()
            name = item.data(Qt.ItemDataRole.UserRole) if item else None
            if not isinstance(name, str):
                return
            answer = QMessageBox.question(dialog, "Remove Remote", f"Remove remote '{name}'?")
            if answer == QMessageBox.StandardButton.Yes:
                try:
                    self.git.remove_remote(self.repository, name)
                    populate()
                except GitRepositoryError as error:
                    self.show_error(str(error))

        add_button.clicked.connect(add_remote)
        remove_button.clicked.connect(remove_remote)
        populate()
        dialog.exec()

    def create_github_repository(self) -> None:
        logger.info(
            "GitHub repository creation requested: repository_open=%s authenticated=%s",
            self.repository is not None,
            self.github_account is not None,
        )
        if self.repository is None:
            logger.info("GitHub repository creation stopped: no local repository is open.")
            self.show_error("Open a local Git repository before creating a GitHub repository.")
            return
        logger.info("GitHub repository creation: requesting repository name.")
        repository_name, accepted = QInputDialog.getText(
            self,
            "Create GitHub Repository",
            "Repository name:",
            text=self.repository.root.name,
        )
        logger.info("GitHub repository creation: repository-name dialog accepted=%s.", accepted)
        if not accepted:
            return
        if self.github_account is None:
            logger.info("GitHub repository creation stopped: no authenticated GitHub account.")
            self.show_error("Connect GitHub before creating a repository.")
            return
        logger.info("GitHub repository creation: requesting visibility.")
        visibility, accepted = QInputDialog.getItem(
            self,
            "Create GitHub Repository",
            "Visibility:",
            ["Private", "Public"],
            0,
            False,
        )
        logger.info("GitHub repository creation: visibility dialog accepted=%s.", accepted)
        if not accepted:
            return
        account_name = self.github_account.account
        logger.info("GitHub repository creation: requesting confirmation.")
        confirmation = QMessageBox.question(
            self,
            "Confirm GitHub Repository Creation",
            f"Create GitHub repository '{repository_name}' for account '{account_name}'? This contacts GitHub.",
        )
        logger.info("GitHub repository creation: confirmation accepted=%s.", confirmation == QMessageBox.StandardButton.Yes)
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        try:
            credential_store = MacOSKeychainCredentialStore()
            provider = GitHubProvider(self.github_account, credential_store.load)
            logger.info("GitHub repository creation: calling GitHub repository API.")
            remote_repository = provider.create_repository(repository_name, private=visibility == "Private")
            logger.info("GitHub repository creation: GitHub repository API succeeded.")
            add_remote = QMessageBox.question(
                self,
                "Add GitHub Remote",
                f"Add '{remote_repository.clone_url}' as the local origin remote?",
            )
            if add_remote == QMessageBox.StandardButton.Yes:
                self.git.add_remote(self.repository, "origin", remote_repository.clone_url)
                self.refresh()
            QMessageBox.information(self, "GitHub Repository Created", f"Created {remote_repository.full_name} on GitHub.")
        except (AuthenticationError, RemoteProviderError, GitRepositoryError) as error:
            logger.info("GitHub repository creation failed with a sanitized error.")
            self.show_error(str(error))

    def connect_github(self) -> None:
        client_id = os.environ.get("VISUAL_GIT_GITHUB_CLIENT_ID", "")
        client_secret = os.environ.get("VISUAL_GIT_GITHUB_CLIENT_SECRET", "")
        authenticator = GitHubAuthenticator(client_id, client_secret, MacOSKeychainCredentialStore())
        try:
            result = authenticator.connect()
            self.github_account = result.account
            QMessageBox.information(self, "GitHub connected", f"Connected as {result.account.account}.")
        except AuthenticationError as error:
            self.show_error(str(error))

    def disconnect_github(self) -> None:
        if self.github_account is None:
            self.show_error("GitHub is not connected.")
            return
        answer = QMessageBox.question(self, "Disconnect GitHub", "Remove the saved GitHub credential?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            GitHubAuthenticator(
                os.environ.get("VISUAL_GIT_GITHUB_CLIENT_ID", ""),
                os.environ.get("VISUAL_GIT_GITHUB_CLIENT_SECRET", ""),
                MacOSKeychainCredentialStore(),
            ).disconnect(self.github_account)
            self.github_account = None
            QMessageBox.information(self, "GitHub disconnected", "The saved GitHub credential was removed.")
        except AuthenticationError as error:
            self.show_error(str(error))

    def _selected_conflict(self, conflicts_list: QListWidget) -> Path | None:
        item = conflicts_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, Path) else None

    def _resolve_conflict_side(self, dialog: QDialog, conflicts_list: QListWidget, use_current: bool) -> None:
        if self.repository is None:
            return
        path = self._selected_conflict(conflicts_list)
        if path is None:
            return
        side = "current" if use_current else "incoming"
        answer = QMessageBox.question(dialog, "Choose conflict side", f"Use the {side} version for {path.as_posix()}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if use_current:
                self.git.use_current(self.repository, path)
            else:
                self.git.use_incoming(self.repository, path)
            self.refresh()
            self._refresh_conflict_list(conflicts_list, path)
        except GitRepositoryError as error:
            self.show_error(str(error))

    def _refresh_conflict_list(self, conflicts_list: QListWidget, selected_path: Path | None = None) -> None:
        if self.repository is None:
            return
        conflicts = self.git.conflict_state(self.repository).conflicts
        conflicts_list.clear()
        selected_row = -1
        for row, path in enumerate(conflicts):
            item = QListWidgetItem(path.as_posix())
            item.setData(Qt.ItemDataRole.UserRole, path)
            conflicts_list.addItem(item)
            if path == selected_path:
                selected_row = row
        if selected_row >= 0:
            conflicts_list.setCurrentRow(selected_row)

    def _open_conflict_editor(self, conflicts_list: QListWidget) -> None:
        path = self._selected_conflict(conflicts_list)
        if path is None or self.repository is None:
            return
        try:
            self.filesystem.open_path(self.repository.root / path)
        except FilesystemError as error:
            self.show_error(str(error))

    def _mark_conflict_resolved(self, dialog: QDialog, conflicts_list: QListWidget) -> None:
        if self.repository is None:
            return
        path = self._selected_conflict(conflicts_list)
        if path is None:
            return
        try:
            self.git.mark_resolved(self.repository, path)
            self.refresh()
            remaining = self.git.conflict_state(self.repository).conflicts
            if not remaining:
                QMessageBox.information(dialog, "Conflicts resolved", "Git reports no remaining unmerged files.")
                dialog.accept()
            else:
                self._refresh_conflict_list(conflicts_list)
        except GitRepositoryError as error:
            self.show_error(str(error))

    def _selected_branch(self, branches_list: QListWidget) -> str | None:
        item = branches_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, str) else None

    def create_branch(self, dialog: QDialog, branches_list: QListWidget) -> None:
        if self.repository is None:
            return
        name, accepted = QInputDialog.getText(dialog, "Create Branch", "Branch name:")
        if accepted:
            try:
                self.git.create_branch(self.repository, name)
                self.repository = self.git.detect(self.repository.root)
                self.refresh()
                dialog.accept()
            except GitRepositoryError as error:
                self.show_error(str(error))

    def switch_branch(self, dialog: QDialog, branches_list: QListWidget) -> None:
        if self.repository is None:
            return
        name = self._selected_branch(branches_list)
        if name is None:
            return
        try:
            self.git.switch_branch(self.repository, name)
            self.repository = self.git.detect(self.repository.root)
            self.refresh()
            dialog.accept()
        except GitRepositoryError as error:
            self.show_error(str(error))

    def rename_branch(self, dialog: QDialog, branches_list: QListWidget) -> None:
        if self.repository is None:
            return
        old_name = self._selected_branch(branches_list)
        if old_name is None:
            return
        new_name, accepted = QInputDialog.getText(dialog, "Rename Branch", "New branch name:")
        if accepted:
            try:
                self.git.rename_branch(self.repository, old_name, new_name)
                self.repository = self.git.detect(self.repository.root)
                self.refresh()
                dialog.accept()
            except GitRepositoryError as error:
                self.show_error(str(error))

    def delete_branch(self, dialog: QDialog, branches_list: QListWidget) -> None:
        if self.repository is None:
            return
        name = self._selected_branch(branches_list)
        if name is None:
            return
        answer = QMessageBox.question(dialog, "Delete Branch", f"Delete branch '{name}'?")
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self.git.delete_branch(self.repository, name)
                dialog.accept()
            except GitRepositoryError as error:
                self.show_error(str(error))

    def _configure_watcher(self) -> None:
        watched_paths = self.watcher.directories() + self.watcher.files()
        if watched_paths:
            self.watcher.removePaths(watched_paths)
        if self.repository is None:
            return
        directories = []
        files = []
        for directory in self.repository.root.rglob("*"):
            if ".git" in directory.parts:
                continue
            if directory.is_dir():
                directories.append(str(directory))
            elif directory.is_file():
                files.append(str(directory))
        directories.append(str(self.repository.root))
        self.watcher.addPaths(directories)
        self.watcher.addPaths(files)

    def _schedule_reconcile(self, _path: str) -> None:
        if self.repository is not None:
            self.reconcile_timer.start()

    def _reconcile_external_changes(self) -> None:
        if self.repository is not None and self.current_directory is not None:
            self.refresh()


def _update_navigation(self) -> None:
    if self.repository is None or self.current_directory is None:
        self.locations_list.clear()
        self.repository_path_label.setText("")
        self.branch_badge.setText("No branch")
        return

    self.locations_list.clear()
    for label, path in (
        ("Repository Root", self.repository.root),
        ("Current Folder", self.current_directory),
    ):
        item = QListWidgetItem(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon), label)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(str(path))
        self.locations_list.addItem(item)

    self.repository_path_label.setText(str(self.current_directory))

    branch = self.repository.branch or "detached HEAD"
    self.branch_badge.setText(f"  {branch}  ")

    while self.breadcrumb_layout.count():
        item = self.breadcrumb_layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()

    relative = self.current_directory.relative_to(self.repository.root)
    parts = [self.repository.root.name, *relative.parts]
    path = self.repository.root

    for index, part in enumerate(parts):
        if index:
            separator = QLabel("›")
            separator.setObjectName("breadcrumbSeparator")
            self.breadcrumb_layout.addWidget(separator)
            path = path / part

        button = QPushButton(part)
        button.setObjectName("breadcrumbButton")
        button.setFlat(True)
        button.setToolTip(str(path))
        button.clicked.connect(lambda _checked=False, target=path: self._navigate_to(target))
        self.breadcrumb_layout.addWidget(button)

    self.breadcrumb_layout.addStretch()

    def _navigate_location(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, Path):
            self._navigate_to(path)

    def _navigate_to(self, path: Path) -> None:
        if self.repository is None or not path.is_dir():
            return
        try:
            path.relative_to(self.repository.root)
        except ValueError:
            return
        self.current_directory = path
        self.refresh()


def _update_status_bar(self, statuses: dict[Path, object]) -> None:
    if self.repository is None:
        self.statusBar().clearMessage()
        return

    counts: dict[str, int] = {}
    for status in statuses.values():
        counts[status.label.lower()] = counts.get(status.label.lower(), 0) + 1

    branch = self.repository.branch or "detached HEAD"
    total = len(statuses)
    if total:
        summary = "  •  ".join(f"{count} {label}" for label, count in sorted(counts.items()))
        message = f"{branch}   ·   {summary}"
    else:
        message = f"{branch}   ·   Working tree clean"

    self.statusBar().showMessage(message)


def _apply_theme(self, theme: str) -> None:
    dark = theme == "Dark"

    colors = {
        "window": "#111318" if dark else "#F3F5F8",
        "canvas": "#15181D" if dark else "#F3F5F8",
        "surface": "#1B1F26" if dark else "#FFFFFF",
        "surface_alt": "#171A20" if dark else "#F8F9FB",
        "surface_hover": "#222731" if dark else "#F1F3F6",
        "text": "#F2F4F7" if dark else "#171A1F",
        "muted": "#9AA2AE" if dark else "#687180",
        "faint": "#737C89" if dark else "#8B94A1",
        "border": "#2A3038" if dark else "#DDE1E7",
        "border_strong": "#363D47" if dark else "#C9CFD8",
        "accent": "#6D7CFF" if dark else "#4F5DE8",
        "accent_hover": "#8090FF" if dark else "#4050D6",
        "accent_soft": "#252B3A" if dark else "#EEF0FF",
        "selection": "#30384A" if dark else "#E6E9FF",
        "selection_text": "#F4F6FF" if dark else "#242B63",
        "success": "#42C88A" if dark else "#1F9D68",
        "warning": "#E5AE45" if dark else "#B7791F",
        "danger": "#EF6B73" if dark else "#D9485F",
    }

    app = QApplication.instance()
    if app is not None:
        font = QFont("SF Pro Text")
        font.setPointSize(10)
        app.setFont(font)

        app.setStyleSheet(
            """
            QMainWindow, QDialog, QWidget#root { background: %(window)s; }

            QWidget {
                color: %(text)s;
                font-family: "SF Pro Text", "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
                font-size: 13px;
            }

            QFrame#appBar {
                background: %(surface)s;
                border: none;
                border-bottom: 1px solid %(border)s;
            }

            QLabel#brandIcon {
                background: %(accent)s;
                color: white;
                border-radius: 8px;
                font-size: 0px;
            }

            QLabel#brandTitle {
                color: %(text)s;
                font-size: 17px;
                font-weight: 650;
            }

            QLabel#brandSubtitle {
                color: %(faint)s;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
                padding-top: 2px;
            }

            QPushButton#openRepositoryButton {
                background: %(accent)s;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 14px;
                font-weight: 600;
            }

            QPushButton#openRepositoryButton:hover {
                background: %(accent_hover)s;
            }

            QPushButton#openRepositoryButton:pressed {
                background: %(accent)s;
            }

            QLabel#mutedLabel {
                color: %(muted)s;
                font-size: 12px;
            }

            QFrame#contextBar {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
            }

            QLabel#contextIcon {
                background: %(accent_soft)s;
                border-radius: 8px;
            }

            QLabel#repositorySummary {
                color: %(text)s;
                font-size: 14px;
                font-weight: 600;
            }

            QLabel#repositoryPath {
                color: %(muted)s;
                font-size: 11px;
            }

            QLabel#branchBadge, QLabel#countBadge {
                color: %(accent)s;
                background: %(accent_soft)s;
                border: 1px solid %(accent_soft)s;
                border-radius: 8px;
                padding: 5px 9px;
                font-size: 11px;
                font-weight: 650;
            }

            QFrame#toolRail {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
            }

            QLabel#sectionTag {
                color: %(faint)s;
                font-size: 10px;
                font-weight: 750;
                letter-spacing: 0.7px;
                padding: 0 4px;
            }

            QFrame#verticalDivider {
                color: %(border)s;
                background: %(border)s;
                max-width: 1px;
            }

            QPushButton[toolIcon="true"] {
                background: transparent;
                color: %(text)s;
                border: 1px solid transparent;
                border-radius: 9px;
                padding: 0;
            }

            QPushButton[toolIcon="true"]:hover {
                background: %(surface_hover)s;
                border-color: %(border)s;
            }

            QPushButton[toolIcon="true"]:pressed {
                background: %(accent_soft)s;
            }

            QPushButton[toolIcon="true"]:disabled {
                opacity: 0.45;
            }

            QPushButton#primaryButton {
                background: %(accent)s;
                color: white;
                border: none;
                font-weight: 650;
            }

            QPushButton#primaryButton:hover {
                background: %(accent_hover)s;
            }

            QPushButton#primaryButton:pressed {
                background: %(accent)s;
            }

            QComboBox {
                background: %(surface_alt)s;
                color: %(text)s;
                border: 1px solid %(border_strong)s;
                border-radius: 8px;
                padding: 0 10px;
                selection-background-color: %(accent_soft)s;
            }

            QComboBox:hover, QComboBox:focus {
                border-color: %(accent)s;
            }

            QComboBox QAbstractItemView {
                background: %(surface)s;
                color: %(text)s;
                border: 1px solid %(border)s;
                selection-background-color: %(selection)s;
                selection-color: %(selection_text)s;
                padding: 4px;
            }

            QFrame#sidebarPanel {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
            }

            QListWidget#locationsList {
                background: transparent;
                border: none;
                outline: none;
            }

            QListWidget#locationsList::item {
                min-height: 34px;
                padding: 5px 8px;
                border-radius: 7px;
                margin: 1px 0;
            }

            QListWidget#locationsList::item:hover {
                background: %(surface_hover)s;
            }

            QListWidget#locationsList::item:selected {
                background: %(selection)s;
                color: %(selection_text)s;
            }

            QFrame#breadcrumbBar {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                min-height: 34px;
            }

            QPushButton#breadcrumbButton {
                background: transparent;
                border: none;
                color: %(muted)s;
                padding: 5px 6px;
                border-radius: 6px;
            }

            QPushButton#breadcrumbButton:hover {
                background: %(accent_soft)s;
                color: %(accent)s;
            }

            QLabel#breadcrumbSeparator {
                color: %(faint)s;
                padding: 0 2px;
            }

            QFrame#explorerFrame {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
            }

            QTreeWidget#explorerTree {
                background: %(surface)s;
                border: none;
                outline: none;
                show-decoration-selected: 1;
            }

            QTreeWidget#explorerTree::item {
                height: 30px;
                padding: 3px 6px;
                border-radius: 6px;
            }

            QTreeWidget#explorerTree::item:hover {
                background: %(surface_hover)s;
            }

            QTreeWidget#explorerTree::item:selected {
                background: %(selection)s;
                color: %(selection_text)s;
            }

            QHeaderView {
                background: %(surface)s;
            }

            QHeaderView::section {
                background: %(surface)s;
                color: %(faint)s;
                border: none;
                border-bottom: 1px solid %(border)s;
                padding: 8px 7px;
                font-size: 10px;
                font-weight: 750;
            }

            QFrame#workflowHelper, QFrame#changesPanel {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
            }

            QLabel#workflowMessage {
                color: %(text)s;
                font-size: 13px;
                font-weight: 560;
            }

            QLabel#workflowStep {
                color: %(muted)s;
                font-size: 12px;
            }

            QListWidget#changesList {
                background: transparent;
                border: none;
                outline: none;
            }

            QListWidget#changesList::item {
                min-height: 28px;
                padding: 4px 7px;
                border-radius: 6px;
                margin: 1px 0;
            }

            QListWidget#changesList::item:hover {
                background: %(surface_hover)s;
            }

            QListWidget#changesList::item:selected {
                background: %(selection)s;
                color: %(selection_text)s;
            }

            QSplitter::handle {
                background: transparent;
            }

            QSplitter::handle:hover {
                background: %(accent_soft)s;
                border-radius: 3px;
            }

            QStatusBar {
                background: %(surface)s;
                color: %(muted)s;
                border-top: 1px solid %(border)s;
                font-size: 11px;
                padding-left: 8px;
            }

            QMenuBar {
                background: %(surface)s;
                color: %(text)s;
                border-bottom: 1px solid %(border)s;
            }

            QMenuBar::item {
                padding: 6px 9px;
                border-radius: 5px;
            }

            QMenuBar::item:selected {
                background: %(surface_hover)s;
            }

            QMenu {
                background: %(surface)s;
                color: %(text)s;
                border: 1px solid %(border)s;
                padding: 5px;
            }

            QMenu::item {
                padding: 7px 28px 7px 10px;
                border-radius: 5px;
            }

            QMenu::item:selected {
                background: %(selection)s;
                color: %(selection_text)s;
            }

            QTextEdit {
                background: %(surface_alt)s;
                color: %(text)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 8px;
                selection-background-color: %(selection)s;
            }

            QDialog {
                background: %(surface)s;
            }

            QListWidget, QTreeWidget {
                selection-background-color: %(selection)s;
                selection-color: %(selection_text)s;
            }

            QScrollBar:vertical, QScrollBar:horizontal {
                background: transparent;
                border: none;
            }

            QScrollBar::handle {
                background: %(border_strong)s;
                border-radius: 5px;
                min-height: 28px;
                min-width: 28px;
            }

            QScrollBar::handle:hover {
                background: %(faint)s;
            }

            QScrollBar::add-line, QScrollBar::sub-line {
                height: 0;
                width: 0;
            }

            QLineEdit, QInputDialog QLineEdit {
                background: %(surface_alt)s;
                color: %(text)s;
                border: 1px solid %(border_strong)s;
                border-radius: 8px;
                padding: 8px 10px;
            }

            QMessageBox, QInputDialog {
                background: %(surface)s;
            }
            """
            % colors
        )

    # Simple vector brand/context marks, rendered at a larger size.
    if hasattr(self, "brand_icon"):
        pass

    # Give context icons a clean native appearance without introducing
    # external image assets.
    icon_color = QColor(colors["accent"])
    for button, (kind, primary) in self._icon_buttons.items():
        button.setIcon(
            self._vector_icon(
                kind,
                QColor("#FFFFFF") if primary else icon_color,
                size=22,
            )
        )

    if hasattr(self, "branch_badge"):
        self.branch_badge.update()

    def update_selection(self) -> None:
        self.up_button.setEnabled(
            self.current_directory is not None
            and self.repository is not None
            and self.current_directory != self.repository.root
        )

    def selected_path(self) -> Path | None:
        paths = self.selected_paths()
        return paths[0] if paths else None

    def selected_paths(self) -> list[Path]:
        values = {
            value
            for item in self.tree.selectedItems()
            if isinstance(value := item.data(0, Qt.ItemDataRole.UserRole), Path)
        }
        return sorted(
            (path for path in values if not any(parent in values for parent in path.parents)),
            key=lambda path: (len(path.parts), path.name.casefold()),
        )

    def go_up(self) -> None:
        if self.repository is None or self.current_directory is None:
            return
        if self.current_directory != self.repository.root:
            self.current_directory = self.current_directory.parent
            self.refresh()

    def create_folder(self) -> None:
        parent = self._selected_directory()
        if parent is None:
            return
        name, accepted = QInputDialog.getText(self, "New Folder", "Folder name:")
        if accepted:
            try:
                self.filesystem.create_folder(parent, name)
                self.refresh()
            except FilesystemError as error:
                self.show_error(str(error))

    def rename_selected(self) -> None:
        selected = self.selected_path()
        if len(self.selected_paths()) != 1 or selected is None:
            if len(self.selected_paths()) > 1:
                self.show_error("Rename applies to one selected file or folder at a time.")
            return
        name, accepted = QInputDialog.getText(self, "Rename", "New name:", text=selected.name)
        if accepted:
            try:
                self.filesystem.rename(selected, name)
                self.refresh()
            except FilesystemError as error:
                self.show_error(str(error))

    def delete_selected(self) -> None:
        selected = self.selected_paths()
        if not selected:
            return
        names = ", ".join(path.name for path in selected)
        answer = QMessageBox.question(self, "Delete", f"Delete {names}? This changes the filesystem.")
        if answer == QMessageBox.StandardButton.Yes:
            try:
                for path in selected:
                    self.filesystem.delete(path)
                self.refresh()
            except FilesystemError as error:
                self.show_error(str(error))

    def open_selected(self) -> None:
        selected = self.selected_path()
        if selected is None:
            return
        if selected.is_dir():
            self.current_directory = selected
            self.refresh()
            return
        try:
            self.filesystem.open_path(selected)
        except FilesystemError as error:
            self.show_error(str(error))

    def handle_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        self.tree.setCurrentItem(item)
        self.open_selected()

    def _selected_directory(self) -> Path | None:
        selected = self.selected_paths()
        if len(selected) != 1:
            return self.current_directory
        return selected[0] if selected[0].is_dir() else selected[0].parent

    def copy_selected(self) -> None:
        self._set_clipboard("copy")

    def cut_selected(self) -> None:
        self._set_clipboard("move")

    def _set_clipboard(self, mode: str) -> None:
        self.clipboard_paths = self.selected_paths()
        self.clipboard_mode = mode if self.clipboard_paths else None

    def paste(self) -> None:
        if not self.clipboard_paths or self.clipboard_mode is None:
            return
        destination = self._selected_directory()
        if destination is None:
            return
        try:
            if self.clipboard_mode == "copy":
                self.filesystem.copy_items(self.clipboard_paths, destination)
            else:
                self.filesystem.move_items(self.clipboard_paths, destination)
                self.clipboard_paths = []
                self.clipboard_mode = None
            self.refresh()
        except FilesystemError as error:
            self.show_error(str(error))

    def move_dropped(self, sources: list[Path], destination: Path) -> None:
        try:
            self.filesystem.move_items(sources, destination)
            self.refresh()
        except FilesystemError as error:
            self.show_error(str(error))

    def show_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is not None and not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
            self.tree.setCurrentItem(item)
        menu = QMenu(self)
        for action in self._edit_actions(menu):
            menu.addAction(action)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Operation failed", message)