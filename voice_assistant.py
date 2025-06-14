import json
import pyaudio
import edge_tts
import asyncio
import os
import pygame
import time
import re
import wave
import numpy as np
from scipy.io import wavfile
import io
from openai import OpenAI
import logging
import speech_recognition as sr

# 配置日志
logging.basicConfig(filename="voice_assistant.log", level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# DeepSeek API 配置
DEEPSEEK_API_KEY = "sk-663e55d90ca249fcb864af331dac2d5f"  # 替换为你的 Key
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")

# 对话历史
conversation_history = [{"role": "system", "content": "你是一个作业辅导助手，提供准确、简洁的学术解答。回复时请使用纯文本，尽量不使用emoji。"}]

def clean_text(text):
    text = re.sub(r"[*#@!]", "", text)
    return text.replace(" ", "")

async def speak(text, voice="zh-CN-XiaoxiaoNeural", rate="+50%"):
    text = clean_text(text)
    try:
        tts = edge_tts.Communicate(text, voice, rate=rate)
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        if os.path.exists("output.mp3"):
            try:
                time.sleep(0.5)
                os.remove("output.mp3")
            except Exception as e:
                logging.warning(f"Failed to remove output.mp3: {e}")
        await tts.save("output.mp3")
        pygame.mixer.init()
        pygame.mixer.music.load("output.mp3")
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            await asyncio.sleep(0.1)
        return True
    except Exception as e:
        logging.error(f"语音合成错误: {e}")
        return False

def chat_with_deepseek(text, history=None):
    clean_input = clean_text(text)
    messages = history if history is not None else conversation_history
    messages.append({"role": "user", "content": clean_input})

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            max_tokens=2000,
            temperature=0.7
        )
        reply = response.choices[0].message.content
        messages.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        logging.error(f"DeepSeek API 错误：{str(e)}")
        return f"API错误：{str(e)}"

def recognize_with_speech_recognition(audio_path):
    """ 使用 speech_recognition 库识别语音 """
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(audio_path) as source:
            audio = recognizer.record(source)  # 读取音频数据
            text = recognizer.recognize_google(audio, language='zh-CN')  # 使用谷歌API识别中文
            return text
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        logging.error(f"SpeechRecognition 请求错误: {e}")
        return None
    except Exception as e:
        logging.error(f"SpeechRecognition 错误: {e}")
        return None

async def process_voice_input(audio=None):
    logging.debug("Processing voice input")

    if audio:
        try:
            temp_audio = "temp_audio.wav"
            if isinstance(audio, str):
                audio_path = audio
            else:
                # 处理 Gradio 音频输入（可能是元组 (sample_rate, audio_data) 或 bytes）
                if isinstance(audio, tuple):
                    sample_rate, audio_data = audio
                    if not isinstance(audio_data, bytes):
                        audio_data = audio_data.tobytes()
                else:
                    audio_data = audio
                    if not isinstance(audio_data, bytes):
                        audio_data = audio_data.tobytes()

                # 保存并转换音频为 WAV 文件
                with wave.open(temp_audio, "wb") as w:
                    w.setnchannels(1)  # 单声道
                    w.setsampwidth(2)  # 16-bit
                    w.setframerate(sample_rate)  # 使用原始采样率
                    w.writeframes(audio_data)
                audio_path = temp_audio

            # 使用 speech_recognition 识别
            text = recognize_with_speech_recognition(audio_path)
            if text:
                cleaned_text = clean_text(text)
                response = chat_with_deepseek(cleaned_text)
                return cleaned_text, response
            return "", "未识别到语音"

        except Exception as e:
            logging.error(f"语音文件处理错误: {str(e)}")
            return "", f"语音处理错误：{str(e)}"
        finally:
            if os.path.exists(temp_audio):
                try:
                    os.remove(temp_audio)
                except Exception as e:
                    logging.warning(f"Failed to remove temp_audio.wav: {e}")
    else:
        # 实时麦克风输入（保持不变，仅供参考）
        audio_stream = pyaudio.PyAudio()
        stream = audio_stream.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=4000)
        stream.start_stream()
        buffered_text = ""
        last_voice_time = time.time()
        SILENCE_LIMIT = 2
        recognizer = sr.Recognizer()

        try:
            with sr.Microphone(sample_rate=16000) as source:
                while True:
                    audio = recognizer.listen(source, timeout=SILENCE_LIMIT)
                    try:
                        text = recognizer.recognize_google(audio, language='zh-CN')
                        buffered_text += text
                        last_voice_time = time.time()
                    except sr.UnknownValueError:
                        if time.time() - last_voice_time > SILENCE_LIMIT and buffered_text:
                            cleaned_text = clean_text(buffered_text)
                            stream.stop_stream()
                            stream.close()
                            audio_stream.terminate()
                            response = chat_with_deepseek(cleaned_text)
                            return cleaned_text, response
                    except sr.RequestError as e:
                        logging.error(f"实时语音识别请求错误: {e}")
                        return "", f"语音识别请求错误：{e}"
                    await asyncio.sleep(0.1)
        except Exception as e:
            stream.stop_stream()
            stream.close()
            audio_stream.terminate()
            logging.error(f"实时语音识别错误: {str(e)}")
            return "", f"语音识别错误：{str(e)}"