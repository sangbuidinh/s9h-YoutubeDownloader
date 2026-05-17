import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, Sequence


DIALOG_PADDING = 16
DIALOG_WIDTH = 460
BUTTON_WIDTH = 14
MESSAGE_WRAP = 400


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

    parent_x = parent.winfo_x()
    parent_y = parent.winfo_y()
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
):
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.resizable(False, False)
    dialog.configure(background=parent.cget("background") if hasattr(parent, "cget") else None)
    dialog.columnconfigure(0, weight=1)

    result = {"value": None}
    shell = ttk.Frame(dialog, padding=DIALOG_PADDING)
    shell.grid(row=0, column=0, sticky="nsew")
    shell.columnconfigure(0, weight=1)

    row = 0
    if message:
        ttk.Label(shell, text=message, wraplength=min(MESSAGE_WRAP, width - 60), justify="left").grid(
            row=row,
            column=0,
            sticky="ew",
            pady=(0, 12),
        )
        row += 1

    body = ttk.Frame(shell)
    body.grid(row=row, column=0, sticky="ew")
    body.columnconfigure(0, weight=1)
    row += 1

    context = DialogContext(parent=parent, dialog=dialog, body=body, result=result)
    if content_builder is not None:
        content_builder(context)

    button_defs = list(buttons or ({"text": "OK", "value": True},))
    button_frame = ttk.Frame(shell)
    button_frame.grid(row=row, column=0, sticky="e", pady=(12, 0))

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
    dialog.lift()
    if modal:
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
        buttons=({"text": "OK", "value": True},),
        default_button="OK",
        cancel_button="OK",
    )


def show_error_dialog(parent: tk.Misc, title: str, message: str, detail: str | None = None):
    full_message = message if not detail else f"{message}\n\nChi tiết:\n{detail}"
    return show_app_dialog(
        parent,
        title,
        message=full_message,
        buttons=({"text": "OK", "value": True},),
        default_button="OK",
        cancel_button="OK",
    )


def show_copy_text_dialog(parent: tk.Misc, title: str, text: str):
    def build_content(context: DialogContext) -> None:
        text_widget = tk.Text(context.body, height=4, width=90, wrap="word", undo=False)
        text_widget.grid(row=0, column=0, sticky="ew")
        text_widget.insert("1.0", text)
        context.initial_focus = text_widget

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
        buttons=({"text": "Đóng", "value": True},),
        default_button="Đóng",
        cancel_button="Đóng",
        content_builder=build_content,
        width=560,
    )
