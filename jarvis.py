import asyncio
import base64
import os
import pyaudio
from dotenv import load_dotenv
from google import genai
from google.genai.types import (
    LiveConnectConfig,
    Content,
    Modality,
    Part,
    PrebuiltVoiceConfig,
    SpeechConfig,
    VoiceConfig,
    Blob,
    Tool,
    GoogleSearch,
)
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Header, Footer, Static, Log
from textual.binding import Binding

# --- CONFIGURATION ---
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")  # Ensure this is in your .env file
MODEL = "gemini-2.0-flash-exp"

# Audio Settings (Gemini Native Audio Standards)
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 512

# --- THE SKEPTIC PERSONA ---
SYSTEM_INSTRUCTION = """
You are Socrates, a Senior Staff Engineer and Code Skeptic. 
Your goal is NOT to write code, but to AUDIT it. 
You are conversational, podcast-style, and slightly cynical but helpful.

Rules:
1. Always ask "Why?" before "How?".
2. If I propose a solution, find the edge case where it breaks.
3. If I ask you to run code, verify safety first.
4. Keep responses punchy and spoken-word style (avoid markdown lists in speech).
5. You have access to tools. Use them to read files or search for docs.
"""


# --- AUDIO HANDLER ---
class AudioLoop:
    def __init__(self):
        self.pya = pyaudio.PyAudio()
        self.out_queue = asyncio.Queue()
        self.running = True
        self.input_device_index = self._resolve_input_device_index()

    def _resolve_input_device_index(self):
        env_index = os.getenv("MIC_DEVICE_INDEX")
        if env_index is not None:
            try:
                return int(env_index)
            except ValueError:
                return None
        try:
            info = self.pya.get_default_input_device_info()
            if not isinstance(info, dict):
                return None
            info_index = info.get("index")
            return int(info_index) if isinstance(info_index, (int, float)) else None
        except Exception:
            return None

    def list_input_devices(self):
        devices = []
        for i in range(self.pya.get_device_count()):
            info = self.pya.get_device_info_by_index(i)
            if not isinstance(info, dict):
                continue
            max_inputs = info.get("maxInputChannels")
            if isinstance(max_inputs, (int, float)) and max_inputs > 0:
                devices.append(
                    {
                        "index": info.get("index"),
                        "name": info.get("name"),
                        "channels": max_inputs,
                    }
                )
        return devices

    async def listen_stream(self, session):
        """Captures Mic and sends to Gemini"""
        if self.input_device_index is None:
            raise RuntimeError("No input audio device found")
        mic_stream = self.pya.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=self.input_device_index,
            frames_per_buffer=CHUNK_SIZE,
        )
        try:
            while self.running:
                data = await asyncio.to_thread(
                    mic_stream.read, CHUNK_SIZE, exception_on_overflow=False
                )
                await session.send_realtime_input(
                    audio=Blob(
                        data=data,
                        mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}",
                    )
                )
        except Exception as e:
            print(f"Mic Error: {e}")
        finally:
            mic_stream.stop_stream()
            mic_stream.close()

    async def play_stream(self):
        """Plays audio from Gemini"""
        spk_stream = self.pya.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
            frames_per_buffer=CHUNK_SIZE,
        )
        while self.running:
            data = await self.out_queue.get()
            if data:
                await asyncio.to_thread(spk_stream.write, data)


# --- TUI INTERFACE ---
class TodoWidget(Static):
    """A widget to display current tasks/critiques"""

    def update_todo(self, text):
        self.update(f"[bold red]Current Critique:[/]\n{text}")


class JarvisApp(App):
    CSS = """
    Screen { layout: grid; grid-size: 2; grid-columns: 2fr 1fr; }
    #left-pane { height: 100%; border-right: solid green; }
    #right-pane { height: 100%; }
    Log { background: $surface; color: $text; }
    TodoWidget { background: $boost; padding: 1; height: 100%; border: solid red; }
    """

    BINDINGS = [Binding("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="left-pane"):
            yield Log(id="chat_log", highlight=True)
        with Container(id="right-pane"):
            yield TodoWidget("Waiting for code to roast...", id="todo_box")
        yield Footer()

    async def on_mount(self):
        """Start the Voice Loop when UI loads"""
        self.audio = AudioLoop()
        self.client = genai.Client(
            api_key=API_KEY, http_options={"api_version": "v1beta"}
        )

        # Start the background task
        asyncio.create_task(self.run_voice_session())

    async def run_voice_session(self):
        log = self.query_one(Log)

        log.write_line("[green]Socrates is listening...[/]")
        devices = self.audio.list_input_devices()
        if not devices:
            log.write_line("[red]No microphone detected.[/]")
        else:
            log.write_line(
                f"[cyan]Mic device index: {self.audio.input_device_index}[/]"
            )

        # Configure Voice & Tools
        config = LiveConnectConfig(
            response_modalities=[Modality.AUDIO, Modality.TEXT],
            system_instruction=Content(parts=[Part(text=SYSTEM_INSTRUCTION)]),
            speech_config=SpeechConfig(
                voice_config=VoiceConfig(
                    prebuilt_voice_config=PrebuiltVoiceConfig(
                        voice_name="Puck"
                    )  # Deep voice
                )
            ),
            # Add Google Search Tool for "Latest Info" and Local File Access
            tools=[Tool(google_search=GoogleSearch()), read_file_content],
        )

        async with self.client.aio.live.connect(model=MODEL, config=config) as session:
            # Start Mic & Speaker Tasks
            try:
                asyncio.create_task(self.audio.listen_stream(session))
            except Exception as e:
                log.write_line(f"[red]Mic Error: {e}[/]")
            asyncio.create_task(self.audio.play_stream())

            while True:
                async for response in session.receive():
                    server_content = response.server_content

                    if server_content is None:
                        continue

                    if server_content.interrupted:
                        while not self.audio.out_queue.empty():
                            self.audio.out_queue.get_nowait()
                        continue

                    # Handle Model Audio Output
                    model_turn = server_content.model_turn
                    if model_turn:
                        for part in model_turn.parts or []:
                            # Audio
                            if part.inline_data:
                                audio_data = part.inline_data.data
                                if isinstance(audio_data, str):
                                    audio_data = base64.b64decode(audio_data)
                                self.audio.out_queue.put_nowait(audio_data)
                            # Text (Logs)
                            if part.text:
                                log.write_line(f"Socrates: {part.text}")

                    # Handle Tool Calls (The "Hands")
                    if response.tool_call:
                        tool_responses = []
                        for fc in response.tool_call.function_calls:
                            name = fc.name
                            args = fc.args
                            log.write_line(f"[yellow]Tool Call: {name}({args})[/]")

                            result = {"error": "Unknown tool"}
                            if name == "read_file_content":
                                filepath = args.get("filepath")
                                result = {"content": read_file_content(filepath)}

                            tool_responses.append(
                                {
                                    "name": name,
                                    "response": result,
                                    "id": fc.id,
                                }
                            )

                        await session.send(
                            tool_response={"function_responses": tool_responses}
                        )
                        log.write_line("[green]Tool responses sent.[/]")

    def on_quit(self):
        self.audio.running = False


# --- TOOLS ---
def read_file_content(filepath: str):
    """Reads a local file for review"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"


if __name__ == "__main__":
    app = JarvisApp()
    app.run()
