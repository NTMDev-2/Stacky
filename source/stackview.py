import tkinter as tk


class StackViewer(tk.Tk):
    def __init__(self, stack, interpreter):
        super().__init__()

        self.geometry("500x450")
        self.minsize(400, 300)

        self.stack = stack
        self.interpreter = interpreter
        self.running = False
        self.root = self

        self.title("Stacky")

        self.text_frame = tk.Frame(self, bg="white")
        self.text_frame.pack(side=tk.TOP,fill=tk.X)
        self.label = tk.Label(
            self.text_frame,
            text="Global Stack",
            font=("Consolas", 12, "italic", "bold"),
            fg="black",
            bg="white"
        )
        self.label.pack(side=tk.TOP, fill=tk.X)

        self.listbox = tk.Listbox(
            self.root,
            font=("Consolas", 12, 'bold'),
            width=30,
            height=20,
            bg='black'
        )
        self.scrollbar = tk.Scrollbar(
            self.listbox,
            orient=tk.VERTICAL,
            command=self.listbox.yview
        )
        self.listbox.config(yscrollcommand=self.scrollbar.set)

        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.stepButton = tk.Button(
            self.root,
            text="Step",
            font=("Consolas", 12, 'italic'),
            width=10,
            command=self.step,
            bg='yellow'
        )
        self.runButton = tk.Button(
            self.root,
            text="Run",
            font=("Consolas", 12, "bold"),
            width=10,
            command=self.run,
            bg='lime',
        )
        self.rerunButton = tk.Button(
            self.root,
            text="Rerun",
            font=("Consolas", 12, 'bold'),
            width=10,
            command=self.rerun,
            bg='red'
        )
        self.resetButton = tk.Button(
            self.root,
            text="Reset",
            font=("Consolas", 12, 'bold'),
            width=10,
            command=lambda: interpreter.rerun(False),
            bg='red'
        )
        self.runButton.pack()
        self.stepButton.pack()
        self.resetButton.pack()
        self.rerunButton.pack()

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after_id = None
        self.update()

    def update(self):
        yview = self.listbox.yview()

        self.listbox.delete(0, tk.END)

        for i, value in enumerate(reversed(self.stack)):
            self.listbox.insert(tk.END, f"[{i}]: {value!r}")

            self.listbox.itemconfig(i, foreground='white')

        self.listbox.yview_moveto(yview[0])

        self.after_id = self.root.after(50, self.update)
    def step(self):
        self.pause()
        self.interpreter.step()
    def run(self):
        if self.running:
            return

        self.running = True
        self.run_step()
    def run_step(self):
        if not self.running:
            return

        if self.interpreter.step():
            self.root.after(50, self.run_step)
        else:
            self.pause()
    def pause(self):
        self.running = False
    def rerun(self):
        self.interpreter.rerun()

    def close(self):
        if self.after_id is not None:
            self.after_cancel(self.after_id)
            self.after_id = None
        self.destroy()
        raise SystemExit
    def _mainloop(self):
        self.root.mainloop()
class LocalsViewer(tk.Toplevel):
    def __init__(self, root, local_stack, local_var_stack, interpreter):
        super().__init__(root)

        self.local_stack = local_stack
        self.local_var_stack = local_var_stack
        self.interpreter = interpreter

        self.title("Stacky - Locals")
        self.configure(bg="black")

        self.localstack_frame = tk.Frame(self, bg="black")

        self.localstack_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.label_localstack = tk.Label(
            self.localstack_frame,
            text="Local Stack",
            font=("Consolas", 12, "italic", "bold"),
            bg="white",
            fg="black"
        )
        self.label_localstack.pack(side=tk.TOP, fill=tk.X)

        self.listbox_localstack = tk.Listbox(
            self.localstack_frame,
            font=("Consolas", 12, "bold"),
            width=30,
            height=20,
            bg="black",
            fg="white",
            justify="right"
        )

        self.scrollbar_localstack = tk.Scrollbar(
            self.localstack_frame,
            orient=tk.VERTICAL,
            command=self.listbox_localstack.yview
        )

        self.listbox_localstack.config(
            yscrollcommand=self.scrollbar_localstack.set
        )

        self.scrollbar_localstack.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox_localstack.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after_id = None
        self.refresh()

    def close(self):
        if self.after_id is not None:
            self.after_cancel(self.after_id)
            self.after_id = None

        self.destroy()
    def refresh(self):
        yview1 = self.listbox_localstack.yview()
        self.listbox_localstack.delete(0, tk.END)

        for i, value in enumerate(reversed(self.local_stack)):
            self.listbox_localstack.insert(tk.END, f"[{list(reversed(self.interpreter.scopes_stack))[i]}]: {value!r}")

            self.listbox_localstack.itemconfig(i, foreground='white')

        self.listbox_localstack.yview_moveto(yview1[0])

        self.after_id = self.after(50, self.refresh)
