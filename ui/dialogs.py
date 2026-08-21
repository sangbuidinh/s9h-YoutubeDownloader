import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, Sequence


DIALOG_PADDING = 14
DIALOG_WIDTH = 500
BUTTON_WIDTH = 14
MESSAGE_WRAP = 440
CLOSE_BUTTON_TEXT = {"hủy", "đóng", "cancel", "close", "no"}
TITLE_TRANSLATIONS = {
    "Error": "Lỗi",
    "Warning": "Cảnh báo",
    "Confirm": "Xác nhận",
    "Info": "Thông báo",
}
BUTTON_TRANSLATIONS = {
    "OK": "Đóng",
    "Cancel": "Hủy",
    "Yes": "Có",
    "No": "Không",
    "Copy": "Sao chép",
    "Close": "Đóng",
    "Retry": "Thử lại",
}


@dataclass
class DialogContext:
    parent: tk.Misc
    dialog: tk.Toplevel
    body: ttk.Frame
    result: dict
    initial_focus: tk.Widget | None = None

    def close(self, value=None) -> None:
        self.result["value"] = value
        self.dialog.destroy()


# All application popups must use this shared dialog helper to keep style, modality, focus, and centering consistent.
def center_dialog_over_parent(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    parent.update_idletasks()
    dialog.update_idletasks()

    parent_x = parent.winfo_rootx()
    parent_y = parent.winfo_rooty()
    parent_w = parent.winfo_width()
    parent_h = parent.winfo_height()
    if parent_w <= 1 or parent_h <= 1:
        parent_x = 0
        parent_y = 0
        parent_w = parent.winfo_screenwidth()
        parent_h = parent.winfo_screenheight()

    dialog_w = dialog.winfo_reqwidth()
    dialog_h = dialog.winfo_reqheight()
    x = parent_x + (parent_w - dialog_w) // 2
    y = parent_y + (parent_h - dialog_h) // 2
    dialog.geometry(f"+{max(0, x)}+{max(0, y)}")


def _dialog_colors(parent: tk.Misc) -> dict[str, str]:
    app_bg = "#f4f7fb"
    try:
        parent_bg = parent.cget("background")
        if isinstance(parent_bg, str) and parent_bg:
            app_bg = parent_bg
    except tk.TclError:
        pass
    return {
        "app_bg": app_bg,
        "panel_bg": app_bg,
        "field_bg": "#ffffff",
        "border": "#d7dee8",
        "text": "#1f2937",
        "muted": "#5f6b7a",
        "primary": "#0a66c2",
        "primary_active": "#0858a8",
        "danger": "#b42318",
        "danger_active": "#941b12",
    }


def _ensure_dialog_styles(parent: tk.Misc) -> dict[str, str]:
    colors = _dialog_colors(parent)
    style = ttk.Style(parent)
    families = set(tkfont.families(parent))
    ui_family = "Segoe UI" if "Segoe UI" in families else tkfont.nametofont("TkDefaultFont").actual("family")
    mono_family = next(
        (candidate for candidate in ("Cascadia Mono", "Consolas", "Courier New") if candidate in families),
        tkfont.nametofont("TkFixedFont").actual("family"),
    )
    colors["ui_family"] = ui_family
    colors["mono_family"] = mono_family

    style.configure("Dialog.TFrame", background=colors["app_bg"])
    style.configure(
        "DialogBody.TFrame",
        background=colors["panel_bg"],
        bordercolor=colors["border"],
        lightcolor=colors["border"],
        darkcolor=colors["border"],
        relief="solid",
        borderwidth=1,
    )
    style.configure("DialogContent.TFrame", background=colors["panel_bg"])
    style.configure("DialogButtonBar.TFrame", background=colors["panel_bg"])
    style.configure(
        "DialogTitle.TLabel",
        background=colors["panel_bg"],
        foreground=colors["text"],
        font=(ui_family, 11, "bold"),
    )
    style.configure(
        "DialogMessage.TLabel",
        background=colors["panel_bg"],
        foreground=colors["text"],
        font=(ui_family, 9),
    )
    style.configure(
        "DialogMuted.TLabel",
        background=colors["panel_bg"],
        foreground=colors["muted"],
        font=(ui_family, 8),
    )
    style.configure(
        "DialogPrimary.TButton",
        background=colors["primary"],
        foreground="#ffffff",
        bordercolor=colors["primary"],
        focuscolor="#bfdbfe",
        padding=(12, 6),
        font=(ui_family, 9, "bold"),
    )
    style.map(
        "DialogPrimary.TButton",
        background=[("active", colors["primary_active"]), ("pressed", "#074a8f"), ("disabled", "#d8dee6")],
        foreground=[("disabled", "#758195")],
        bordercolor=[("active", colors["primary_active"]), ("disabled", "#d8dee6")],
    )
    style.configure(
        "DialogSecondary.TButton",
        background="#f8fafc",
        foreground=colors["text"],
        bordercolor=colors["border"],
        focuscolor="#d7ebff",
        padding=(11, 6),
        font=(ui_family, 9),
    )
    style.map(
        "DialogSecondary.TButton",
        background=[("active", "#eef2f7"), ("pressed", "#e5ebf2"), ("disabled", "#eef2f7")],
        foreground=[("disabled", "#8a95a3")],
        bordercolor=[("active", "#c8d3df"), ("disabled", "#d8dee6")],
    )
    style.configure(
        "DialogDanger.TButton",
        background=colors["danger"],
        foreground="#ffffff",
        bordercolor=colors["danger"],
        focuscolor="#fecaca",
        padding=(12, 6),
        font=(ui_family, 9, "bold"),
    )
    style.map(
        "DialogDanger.TButton",
        background=[("active", colors["danger_active"]), ("pressed", "#7f1d1d"), ("disabled", "#eef2f7")],
        foreground=[("disabled", "#8a95a3")],
        bordercolor=[("active", colors["danger_active"]), ("disabled", "#d8dee6")],
    )
    return colors


def _button_style(button_def: dict, cancel_button: str | None) -> str:
    explicit_style = button_def.get("style")
    if explicit_style:
        return explicit_style

    text = str(button_def.get("text", ""))
    normalized = text.strip().lower()
    if cancel_button and text == cancel_button:
        return "DialogSecondary.TButton"
    if normalized in CLOSE_BUTTON_TEXT or button_def.get("value") is False:
        return "DialogSecondary.TButton"
    if button_def.get("danger"):
        return "DialogDanger.TButton"
    return "DialogPrimary.TButton"


def _localized_title(title: str) -> str:
    return TITLE_TRANSLATIONS.get(title, title)


def _localized_button_text(text: str) -> str:
    return BUTTON_TRANSLATIONS.get(text, text)


def _localized_button_name(name: str | None) -> str | None:
    return _localized_button_text(name) if name else None


def _strip_log_prefix(message: str) -> str:
    text = (message or "").strip()
    for prefix in ("[ERROR]", "[WARNING]", "[INFO]", "[SUCCESS]", "[SKIP]"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _body_heading(title: str, heading: str | None) -> str:
    text = (heading or "").strip()
    if not text:
        return ""
    return "" if text == title.strip() else text


def _restore_iconic_parent_for_modal(parent: tk.Misc, modal: bool) -> bool:
    if not modal:
        return False

    try:
        state_getter = getattr(parent, "state", None) or getattr(parent, "wm_state", None)
        if state_getter is None or str(state_getter()).strip().lower() != "iconic":
            return False
        parent.deiconify()
    except (AttributeError, tk.TclError):
        return False

    for method_name in ("update_idletasks", "lift"):
        try:
            getattr(parent, method_name)()
        except (AttributeError, tk.TclError):
            pass
    return True


def show_app_dialog(
    parent: tk.Misc,
    title: str,
    message: str | None = None,
    buttons: Sequence[dict] | None = None,
    default_button: str | None = None,
    cancel_button: str | None = None,
    content_builder: Callable[[DialogContext], None] | None = None,
    width: int = DIALOG_WIDTH,
    modal: bool = True,
    heading: str | None = None,
):
    _restore_iconic_parent_for_modal(parent, modal)
    colors = _ensure_dialog_styles(parent)
    title = _localized_title(title)
    heading = _body_heading(title, _localized_title(heading) if heading else None)
    default_button = _localized_button_name(default_button)
    cancel_button = _localized_button_name(cancel_button)
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title(title)
    dialog.transient(parent)
    dialog.resizable(False, False)
    dialog.configure(background=colors["app_bg"])
    dialog.columnconfigure(0, weight=1)

    result = {"value": None}
    shell = ttk.Frame(dialog, padding=DIALOG_PADDING, style="Dialog.TFrame")
    shell.grid(row=0, column=0, sticky="nsew")
    shell.columnconfigure(0, weight=1)

    card = ttk.Frame(shell, padding=(18, 16), style="DialogBody.TFrame")
    card.grid(row=0, column=0, sticky="nsew")
    card.columnconfigure(0, weight=1)

    row = 0
    if heading:
        ttk.Label(card, text=heading, style="DialogTitle.TLabel").grid(
            row=row,
            column=0,
            sticky="ew",
            pady=(0, 10 if message else 8),
        )
        row += 1

    if message:
        ttk.Label(
            card,
            text=message,
            wraplength=min(MESSAGE_WRAP, width - 60),
            justify="left",
            style="DialogMessage.TLabel",
        ).grid(
            row=row,
            column=0,
            sticky="ew",
            pady=(0, 14),
        )
        row += 1

    body = ttk.Frame(card, style="DialogContent.TFrame")
    body.grid(row=row, column=0, sticky="ew")
    body.columnconfigure(0, weight=1)
    row += 1

    context = DialogContext(parent=parent, dialog=dialog, body=body, result=result)
    if content_builder is not None:
        content_builder(context)

    button_defs = [dict(button_def) for button_def in (buttons or ({"text": "Đóng", "value": True},))]
    for button_def in button_defs:
        button_def["text"] = _localized_button_text(str(button_def.get("text", "")))
    button_frame = ttk.Frame(card, style="DialogButtonBar.TFrame")
    button_frame.grid(row=row, column=0, sticky="e", pady=(16, 0))

    button_widgets: dict[str, ttk.Button] = {}

    def make_command(button_def: dict):
        def command():
            callback = button_def.get("command")
            if callback is not None:
                callback(context)
                return
            context.close(button_def.get("value", button_def.get("text")))

        return command

    for index, button_def in enumerate(button_defs):
        text = button_def.get("text", "")
        button = ttk.Button(
            button_frame,
            text=text,
            width=button_def.get("width", BUTTON_WIDTH),
            style=_button_style(button_def, cancel_button),
            command=make_command(button_def),
        )
        button.grid(row=0, column=index, padx=(0 if index == 0 else 8, 0))
        button_widgets[text] = button

    if cancel_button:
        cancel_value = None
        for button_def in button_defs:
            if button_def.get("text") == cancel_button:
                cancel_value = button_def.get("value", cancel_button)
                break
        dialog.protocol("WM_DELETE_WINDOW", lambda: context.close(cancel_value))
        dialog.bind("<Escape>", lambda _event: context.close(cancel_value))
    else:
        dialog.protocol("WM_DELETE_WINDOW", lambda: context.close(None))
        dialog.bind("<Escape>", lambda _event: context.close(None))

    if default_button and default_button in button_widgets:
        dialog.bind("<Return>", lambda _event: button_widgets[default_button].invoke())

    center_dialog_over_parent(dialog, parent)
    dialog.deiconify()
    dialog.lift()
    if modal:
        dialog.wait_visibility()
        dialog.grab_set()
    focus_widget = context.initial_focus or button_widgets.get(default_button or "")
    if focus_widget is not None:
        focus_widget.focus_force()
    else:
        dialog.focus_force()

    if modal:
        dialog.wait_window()
        return result["value"]
    return context


def show_confirm_dialog(
    parent: tk.Misc,
    title: str,
    message: str,
    confirm_text: str,
    cancel_text: str,
) -> bool:
    return bool(
        show_app_dialog(
            parent,
            title,
            message=message,
            buttons=(
                {"text": cancel_text, "value": False},
                {"text": confirm_text, "value": True},
            ),
            default_button=cancel_text,
            cancel_button=cancel_text,
        )
    )


def show_info_dialog(parent: tk.Misc, title: str, message: str):
    return show_app_dialog(
        parent,
        title,
        message=message,
        buttons=({"text": "Đóng", "value": True},),
        default_button="Đóng",
        cancel_button="Đóng",
    )


def show_error_dialog(
    parent: tk.Misc,
    title: str,
    message: str,
    detail: str | None = None,
    heading: str | None = None,
):
    clean_message = _strip_log_prefix(message)
    full_message = clean_message if not detail else f"{clean_message}\n\nChi tiết kỹ thuật:\n{detail}"
    return show_app_dialog(
        parent,
        title,
        message=full_message,
        buttons=({"text": "Đóng", "value": True, "style": "DialogDanger.TButton"},),
        default_button="Đóng",
        cancel_button="Đóng",
        heading=heading,
    )


def show_copy_text_dialog(parent: tk.Misc, title: str, text: str):
    copy_state: dict[str, object] = {}

    def copy_text(context: DialogContext) -> None:
        context.parent.clipboard_clear()
        context.parent.clipboard_append(text)
        status_var = copy_state.get("status_var")
        text_widget = copy_state.get("text_widget")
        if isinstance(status_var, tk.StringVar):
            status_var.set("Đã copy.")
        if isinstance(text_widget, tk.Text):
            text_widget.focus_set()

    def build_content(context: DialogContext) -> None:
        colors = _ensure_dialog_styles(context.parent)
        context.body.columnconfigure(0, weight=1)

        text_frame = ttk.Frame(context.body, style="DialogContent.TFrame")
        text_frame.grid(row=0, column=0, sticky="ew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        text_widget = tk.Text(
            text_frame,
            height=7,
            width=72,
            wrap="word",
            undo=False,
            background=colors["field_bg"],
            foreground=colors["text"],
            insertbackground=colors["text"],
            selectbackground="#d7eaff",
            selectforeground="#102a43",
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=colors["border"],
            highlightcolor="#8fc5f5",
            padx=8,
            pady=6,
            font=(colors["mono_family"], 9),
        )
        text_widget.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=text_widget.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text_widget.configure(yscrollcommand=scrollbar.set)
        text_widget.insert("1.0", text)
        context.initial_focus = text_widget

        status_var = tk.StringVar(value="")
        copy_state["status_var"] = status_var
        copy_state["text_widget"] = text_widget
        ttk.Label(
            context.body,
            textvariable=status_var,
            style="DialogMuted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        def select_all(_event=None):
            text_widget.focus_set()
            text_widget.tag_add("sel", "1.0", "end-1c")
            text_widget.mark_set("insert", "1.0")
            return "break"

        def block_edit(event):
            if event.keysym in ("Left", "Right", "Home", "End", "Up", "Down"):
                return None
            if event.keysym in ("Escape", "Return"):
                context.close(True)
                return "break"
            if event.state & 0x4 and event.keysym.lower() in ("a", "c"):
                return None
            return "break"

        text_widget.bind("<Control-a>", select_all)
        text_widget.bind("<Control-A>", select_all)
        text_widget.bind("<KeyPress>", block_edit)
        context.dialog.after(50, select_all)

    return show_app_dialog(
        parent,
        title,
        buttons=(
            {"text": "Đóng", "value": True},
            {"text": "Sao chép", "command": copy_text, "width": 12},
        ),
        default_button="Đóng",
        cancel_button="Đóng",
        content_builder=build_content,
        width=620,
    )
