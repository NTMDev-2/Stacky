import tkinter as tk

REFRESH_MS = 50
RUN_STEP_MS = 50

HEADER_FONT = ("Consolas", 12, "italic", "bold")
LIST_FONT = ("Consolas", 12, "bold")
BUTTON_FONT = ("Consolas", 12, "bold")

class ListPanel(tk.Frame):
    def __init__(self, master, title, justify="left"):
        super().__init__(master, bg="black")

        self._title = None
        self._lines = None

        self.label = tk.Label(
            self, font=HEADER_FONT, fg="black", bg="white"
        )
        self.label.pack(side=tk.TOP, fill=tk.X)

        body = tk.Frame(self, bg="black")
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(
            body,
            font=LIST_FONT,
            bg="black",
            fg="white",
            justify=justify,
            activestyle="none",
            exportselection=False,
            highlightthickness=0,
        )
        self.scrollbar = tk.Scrollbar(
            body, orient=tk.VERTICAL, command=self.listbox.yview
        )
        self.listbox.config(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.set_title(title)

    def set_title(self, title):
        if title != self._title:
            self._title = title
            self.label.config(text=title)

    def show(self, lines):
        if lines == self._lines:
            return
        self._lines = list(lines)

        top = self.listbox.yview()[0]
        self.listbox.delete(0, tk.END)
        if lines:
            self.listbox.insert(tk.END, *lines)
        self.listbox.yview_moveto(top)
class _Polled:
    _poll_id = None

    def _poll(self):
        try:
            self.refresh()
        finally:
            self._poll_id = self.after(REFRESH_MS, self._poll)
    def _stop_polling(self):
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
    def destroy(self):
        self._stop_polling()
        super().destroy()

class StackViewer(_Polled, tk.Tk):
    def __init__(self, stack, interpreter):
        super().__init__()

        self.geometry("500x450")
        self.minsize(400, 300)
        self.title("Stacky")

        self.stack = stack
        self.interpreter = interpreter
        self.root = self
        self.running = False
        self.run_after_id = None

        self.status = tk.Label(
            self,
            text="Ready",
            anchor="w",
            justify="left",
            wraplength=480,
            font=("Consolas", 10),
            bg="#222222",
            fg="white",
        )
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

        self.button_frame = tk.Frame(self, bg="white")
        self.button_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.panel = ListPanel(self, "Global Stack")
        self.panel.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.runButton = self._make_button("Run", self.run, "lime")
        self.stepButton = self._make_button("Step", self.step, "yellow")
        self.resetButton = self._make_button("Reset", self.reset, "red")
        self.rerunButton = self._make_button("Rerun", self.rerun, "red")
        self.reloadButton = self._make_button("Reload", self.reload, "yellow")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self._poll()

    def _make_button(self, text, command, bg):
        button = tk.Button(
            self.button_frame,
            text=text,
            font=BUTTON_FONT,
            command=command,
            bg=bg,
        )
        button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return button
    def refresh(self):
        lines = [f"[{i}]: {value!r}" for i, value in enumerate(reversed(self.stack))]
        self.panel.set_title(f"Global Stack ({len(lines)})")
        self.panel.show(lines)
    def set_status(self, text, error=False):
        self.status.config(text=text, fg="#ff0000" if error else "white")

    def _do_step(self):
        try:
            more = self.interpreter.step()
        except Exception as exc:
            self.pause()
            self.set_status(f"Error: {type(exc).__name__}: {exc}", error=True)
            return "error"

        if not more:
            self.pause()
            self.set_status("Finished")
            return "done"
        return "ok"
    def step(self):
        self.pause()
        if self._do_step() == "ok":
            self.set_status("Paused")
        self.refresh()
    def run(self):
        if self.running:
            self.pause()
            self.set_status("Paused")
            return

        self.running = True
        self.runButton.config(text="Pause")
        self.set_status("Running...")
        self._run_step()
    def _run_step(self):
        self.run_after_id = None
        if not self.running:
            return

        result = self._do_step()
        self.refresh()
        if result == "ok":
            self.run_after_id = self.after(RUN_STEP_MS, self._run_step)
    def pause(self):
        self.pause_quietly()
        self.runButton.config(text="Run")
    def pause_quietly(self):
        self.running = False
        if self.run_after_id is not None:
            try:
                self.after_cancel(self.run_after_id)
            except tk.TclError:
                pass
            self.run_after_id = None

    def _guarded(self, action, done_text):
        self.pause()
        try:
            action()
        except Exception as exc:
            self.set_status(f"Error: {type(exc).__name__}: {exc}", error=True)
        else:
            self.set_status(done_text)
        self.refresh()
    def reset(self):
        self._guarded(lambda: self.interpreter.rerun(False), "Reset")
    def rerun(self):
        self._guarded(self.interpreter.rerun, "Finished")
    def reload(self):
        self._guarded(self.interpreter.reloadFile, "Reloaded and finished")

    def close(self):
        self.destroy() 
    def destroy(self):
        self.pause_quietly()
        super().destroy()
    def _mainloop(self):
        self.root.mainloop()
class LocalsViewer(_Polled, tk.Toplevel):
    def __init__(self, root, local_stack, local_var_stack, interpreter):
        super().__init__(root)
        self.attributes("-topmost", False)

        self.local_stack = local_stack
        self.local_var_stack = local_var_stack
        self.interpreter = interpreter

        self.title("Stacky - Locals")
        self.geometry("420x480")
        self.minsize(300, 250)
        self.configure(bg="black")

        self.stack_panel = ListPanel(self, "Local Stack", justify="left")
        self.stack_panel.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.vars_panel = ListPanel(self, "Local Variables", justify='left')
        self.vars_panel.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._poll()
    @staticmethod
    def _at(seq, index, default):
        return seq[index] if index < len(seq) else default
    def refresh(self):
        scopes = list(self.interpreter.scopes_stack)
        frames = list(getattr(self.interpreter, "stack_frames", []))

        stack_lines = []
        var_lines = []

        for i in reversed(range(len(scopes))):
            name = scopes[i]

            stack = self._at(self.local_stack, i, [])
            stack_lines.append(f"[{name}]: {stack!r}")

            variables = {}
            frame = self._at(frames, i, None)
            if frame is not None:
                variables.update(frame.get("context", {}))
            variables.update(self._at(self.local_var_stack, i, {}))
            for var, value in variables.items():
                var_lines.append(f"[{name}] {var}: {value!r}")

        self.stack_panel.set_title(f"Local Stack ({len(stack_lines)})")
        self.stack_panel.show(stack_lines)
        self.vars_panel.show(var_lines)
