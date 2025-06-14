import gradio as gr
import sqlite3
import matplotlib.pyplot as plt
import time
import io
import re
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, date
from PIL import Image
import pygame
import edge_tts
import os
from voice_assistant import process_voice_input, chat_with_deepseek

# --------------------- 配置和初始化 ---------------------

# 设置日志（使用 UTF-8 编码，最大文件大小 5MB，保留 5 个备份）
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = RotatingFileHandler("app_debug.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s: %(message)s"))
logger.addHandler(handler)

# 配置 Matplotlib 支持中文显示
plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# 全局对话历史
chat_history = []

# --------------------- 工具函数 ---------------------

def log_action(action: str) -> None:
    """记录用户操作到日志文件和调试日志。"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open("user_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {action}\n")
    logger.debug(f"Action logged: {action}")

def get_db_connection():
    """获取数据库连接，使用上下文管理器确保资源释放。"""
    try:
        with sqlite3.connect("homework.db", check_same_thread=False) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS homework (
                    id INTEGER PRIMARY KEY,
                    task TEXT NOT NULL,
                    deadline TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS mistakes (
                    id INTEGER PRIMARY KEY,
                    question TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    image BLOB
                )"""
            )
            yield conn
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        raise

def validate_date_format(deadline: str) -> bool:
    """验证日期格式是否为 YYYY-MM-DD 且不早于今天。"""
    try:
        deadline_date = datetime.strptime(deadline, "%Y-%m-%d").date()
        return deadline_date >= date.today()
    except ValueError:
        return False

async def generate_audio(text: str, tone: str = "温柔姐姐") -> bytes:
    """生成语音音频文件并返回字节数据。

    Args:
        text: 要转换为语音的文本。
        tone: 语音语气，可选 "温柔姐姐"、"严厉老师"、"搞笑同学"。

    Returns:
        音频字节数据，或 None（失败时）。
    """
    voice_map = {
        "温柔姐姐": "zh-CN-XiaoxiaoNeural",
        "严厉老师": "zh-CN-YunyangNeural",
        "搞笑同学": "zh-CN-YunxiNeural"
    }
    output_file = "output.mp3"
    log_text = "".join(c for c in text[:50] if ord(c) < 128)
    logger.info(f"Generating audio for text: {log_text}...")

    try:
        voice = voice_map.get(tone, "zh-CN-XiaoxiaoNeural")
        if pygame.mixer.get_init():
            pygame.mixer.quit()
        pygame.mixer.init()
        if os.path.exists(output_file):
            os.remove(output_file)
        tts = edge_tts.Communicate(text, voice, rate="+50%")
        await tts.save(output_file)
        if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
            logger.error("Empty or missing audio file")
            return None
        logger.info(f"Audio file generated: {output_file}, size: {os.path.getsize(output_file)} bytes")
        with open(output_file, "rb") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Audio generation error: {str(e)}")
        return None
    finally:
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
                logger.debug(f"Deleted temporary file: {output_file}")
            except Exception as e:
                logger.warning(f"Failed to delete {output_file}: {e}")

def play_audio(audio_data: bytes) -> None:
    """播放音频数据。"""
    if not audio_data or not isinstance(audio_data, (bytes, bytearray)):
        logger.warning("No valid audio data provided for playback")
        return
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        pygame.mixer.init()
        pygame.mixer.music.load(io.BytesIO(audio_data))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
    except Exception as e:
        logger.error(f"Audio playback error: {str(e)}")

# --------------------- 数据库操作 ---------------------

def reset_ids(cursor: sqlite3.Cursor, conn: sqlite3.Connection, table_name: str) -> None:
    """重置指定表的 ID，按特定顺序重新分配。

    Args:
        cursor: 数据库游标。
        conn: 数据库连接。
        table_name: 表名，"homework" 或 "mistakes"。
    """
    try:
        if table_name == "homework":
            cursor.execute("SELECT task, deadline FROM homework ORDER BY deadline")
            rows = cursor.fetchall()
            cursor.execute("DELETE FROM homework")
            for i, (task, deadline) in enumerate(rows, 1):
                cursor.execute(
                    "INSERT INTO homework (id, task, deadline) VALUES (?, ?, ?)",
                    (i, task, deadline)
                )
        elif table_name == "mistakes":
            cursor.execute("SELECT question, subject, image FROM mistakes ORDER BY id")
            rows = cursor.fetchall()
            cursor.execute("DELETE FROM mistakes")
            for i, (question, subject, image) in enumerate(rows, 1):
                cursor.execute(
                    "INSERT INTO mistakes (id, question, subject, image) VALUES (?, ?, ?, ?)",
                    (i, question, subject, image)
                )
        conn.commit()
    except Exception as e:
        logger.error(f"Reset IDs error for {table_name}: {str(e)}")
        raise

def get_homework_list() -> str:
    """获取作业列表，按截止日期排序。"""
    try:
        conn = next(get_db_connection())
        cursor = conn.cursor()
        cursor.execute("SELECT id, task, deadline FROM homework ORDER BY deadline")
        rows = cursor.fetchall()
        return "\n".join(f"{id}: {task} (截止: {deadline})" for id, task, deadline in rows) or "暂无作业"
    except Exception as e:
        logger.error(f"Get homework list error: {str(e)}")
        return "获取作业列表失败，请稍后重试"

def get_mistakes_list(subject_filter: str = None) -> str:
    """获取错题列表，支持按科目筛选。"""
    try:
        conn = next(get_db_connection())
        cursor = conn.cursor()
        query = "SELECT id, question, subject FROM mistakes"
        params = ()
        if subject_filter and subject_filter != "全部":
            query += " WHERE subject = ?"
            params = (subject_filter,)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return "\n".join(f"{id}: {question} ({subject})" for id, question, subject in rows) or "暂无错题"
    except Exception as e:
        logger.error(f"Get mistakes list error: {str(e)}")
        return "获取错题列表失败，请稍后重试"

def get_mistake_details(mistake_id: str):
    """获取指定错题的详细信息。

    Returns:
        Tuple[str, PIL.Image.Image | None, gr.Update, gr.Update]: 详情文本、图片、ID 输入框更新、图片输入框更新。
    """
    if not mistake_id:
        return "请输入错题 ID（正整数）", None, gr.update(value=""), gr.update(value=None)
    try:
        mistake_id = int(mistake_id)
        if mistake_id <= 0:
            return "错题 ID 必须为正整数", None, gr.update(value=""), gr.update(value=None)
        conn = next(get_db_connection())
        cursor = conn.cursor()
        cursor.execute("SELECT question, subject, image FROM mistakes WHERE id = ?", (mistake_id,))
        row = cursor.fetchone()
        if row:
            question, subject, image = row
            details = f"问题: {question}\n科目: {subject}"
            image_data = Image.open(io.BytesIO(image)) if image else None
            return details, image_data, gr.update(value=""), gr.update(value=None)
        return f"未找到错题 ID {mistake_id}，请检查错题列表", None, gr.update(value=""), gr.update(value=None)
    except ValueError:
        return "请输入有效的错题 ID（正整数）", None, gr.update(value=""), gr.update(value=None)
    except Exception as e:
        logger.error(f"Get mistake details error: {str(e)}")
        return f"获取详情失败：{str(e)}，请稍后重试", None, gr.update(value=""), gr.update(value=None)

def refresh_homework() -> tuple[str, str]:
    """清理过期作业并重新分配 ID。"""
    try:
        conn = next(get_db_connection())
        cursor = conn.cursor()
        today = date.today().strftime("%Y-%m-%d")
        cursor.execute("SELECT COUNT(*) FROM homework WHERE deadline < ?", (today,))
        expired_count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM homework WHERE deadline < ?", (today,))
        conn.commit()
        reset_ids(cursor, conn, "homework")
        updated_list = get_homework_list()
        remaining_count = cursor.execute("SELECT COUNT(*) FROM homework").fetchone()[0]
        log_action(f"清理过期作业: 删除 {expired_count} 条，剩余 {remaining_count} 条")
        return (
            f"已清理 {expired_count} 条过期作业，剩余 {remaining_count} 条",
            updated_list
        )
    except Exception as e:
        logger.error(f"Refresh homework error: {str(e)}")
        return f"清理失败：{str(e)}，请稍后重试", get_homework_list()

# --------------------- 业务逻辑 ---------------------

async def add_homework(task: str, deadline: str, audio_state=gr.State(None)):
    """添加新作业并生成语音反馈。"""
    if not task:
        return "请输入作业内容", get_homework_list(), gr.update(value=""), gr.update(value=None), audio_state
    if not validate_date_format(deadline):
        return "截止日期无效（需不早于今天，格式为 YYYY-MM-DD）", get_homework_list(), gr.update(value=""), gr.update(value=None), audio_state
    try:
        conn = next(get_db_connection())
        cursor = conn.cursor()
        cursor.execute("INSERT INTO homework (task, deadline) VALUES (?, ?)", (task, deadline))
        conn.commit()
        reset_ids(cursor, conn, "homework")
        log_action(f"添加作业: {task}")
        chat_history.append(f"助手: 已添加作业: {task}")
        audio_data = await generate_audio(f"已添加作业：{task}")
        if not audio_data:
            return (
                f"已添加作业：{task}\n（语音反馈生成失败）",
                get_homework_list(),
                gr.update(value=""),
                gr.update(value=None),
                audio_state
            )
        return (
            f"已添加作业：{task}",
            get_homework_list(),
            gr.update(value=""),
            gr.update(value=None),
            audio_data
        )
    except Exception as e:
        logger.error(f"Add homework error: {str(e)}")
        return (
            f"添加作业失败：{str(e)}，请稍后重试",
            get_homework_list(),
            gr.update(value=""),
            gr.update(value=None),
            audio_state
        )

async def delete_homework(homework_id: str, audio_state=gr.State(None)):
    """删除指定作业并生成语音反馈。"""
    try:
        homework_id = int(homework_id)
        if homework_id <= 0:
            return "作业 ID 必须为正整数", get_homework_list(), gr.update(value=""), audio_state
        conn = next(get_db_connection())
        cursor = conn.cursor()
        cursor.execute("SELECT task FROM homework WHERE id = ?", (homework_id,))
        task = cursor.fetchone()
        if task:
            cursor.execute("DELETE FROM homework WHERE id = ?", (homework_id,))
            conn.commit()
            reset_ids(cursor, conn, "homework")
            log_action(f"删除作业: ID {homework_id}")
            chat_history.append(f"助手: 已删除作业: {task[0]}")
            audio_data = await generate_audio(f"已删除作业：{task[0]}")
            if not audio_data:
                return (
                    f"已删除作业: {task[0]}\n（语音反馈生成失败）",
                    get_homework_list(),
                    gr.update(value=""),
                    audio_state
                )
            return (
                f"已删除作业: {task[0]}",
                get_homework_list(),
                gr.update(value=""),
                audio_data
            )
    except ValueError:
        return "请输入有效的作业 ID（正整数）", get_homework_list(), gr.update(value=""), audio_state
    except Exception as e:
        logger.error(f"Delete homework error: {str(e)}")
        return f"删除失败：{str(e)}，请稍后重试", get_homework_list(), gr.update(value=""), audio_state

async def add_mistake(question: str, subject: str, image=None, audio_state=gr.State(None)):
    """添加新错题，支持图片上传并生成语音反馈。"""
    if not question:
        return (
            "请输入错题内容",
            get_mistakes_list(),
            get_stats_data()[0],
            None,
            None,
            gr.update(value=""),
            gr.update(value=None),
            audio_state
        )
    image_data = None
    if image:
        try:
            with open(image, "rb") as f:
                image_data = f.read()
        except Exception as e:
            logger.error(f"Image read error: {str(e)}")
            return (
                f"图片读取失败：{str(e)}，请确保文件有效",
                get_mistakes_list(),
                get_stats_data()[0],
                None,
                None,
                gr.update(value=""),
                gr.update(value=None),
                audio_state
            )
    try:
        conn = next(get_db_connection())
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO mistakes (question, subject, image) VALUES (?, ?, ?)",
            (question, subject, image_data)
        )
        conn.commit()
        reset_ids(cursor, conn, "mistakes")
        log_action(f"记录错题: {question}")
        chat_history.append(f"助手: 已记录错题: {question}")
        audio_data = await generate_audio(f"已记录错题：{question}")
        if not audio_data:
            return (
                f"已记录错题: {question}\n（语音反馈生成失败）",
                get_mistakes_list(),
                get_stats_data()[0],
                None,
                None,
                gr.update(value=""),
                gr.update(value=None),
                audio_state
            )
        return (
            f"已记录错题: {question}",
            get_mistakes_list(),
            get_stats_data()[0],
            None,
            None,
            gr.update(value=""),
            gr.update(value=None),
            audio_data
        )
    except Exception as e:
        logger.error(f"Add mistake error: {str(e)}")
        return (
            f"记录错题失败：{str(e)}，请稍后重试",
            get_mistakes_list(),
            get_stats_data()[0],
            None,
            None,
            gr.update(value=""),
            gr.update(value=None),
            audio_state
        )

async def delete_mistake(mistake_id: str, audio_state=gr.State(None)):
    """删除指定错题并生成语音反馈。"""
    try:
        mistake_id = int(mistake_id)
        if mistake_id <= 0:
            return (
                "错题 ID 必须为正整数",
                get_mistakes_list(),
                get_stats_data()[0],
                None,
                None,
                gr.update(value=""),
                audio_state
            )
        conn = next(get_db_connection())
        cursor = conn.cursor()
        cursor.execute("SELECT question FROM mistakes WHERE id = ?", (mistake_id,))
        question = cursor.fetchone()
        if question:
            cursor.execute("DELETE FROM mistakes WHERE id = ?", (mistake_id,))
            conn.commit()
            reset_ids(cursor, conn, "mistakes")
            log_action(f"删除错题: ID {mistake_id}")
            chat_history.append(f"助手: 已删除错题: {question[0]}")
            audio_data = await generate_audio(f"已删除错题：{question[0]}")
            if not audio_data:
                return (
                    f"已删除错题: {question[0]}\n（语音反馈生成失败）",
                    get_mistakes_list(),
                    get_stats_data()[0],
                    None,
                    None,
                    gr.update(value=""),
                    audio_state
                )
            return (
                f"已删除错题: {question[0]}",
                get_mistakes_list(),
                get_stats_data()[0],
                None,
                None,
                gr.update(value=""),
                audio_state
            )
    except ValueError:
        return (
            "请输入有效的错题 ID（正整数）",
            get_mistakes_list(),
            get_stats_data()[0],
            None,
            None,
            gr.update(value=""),
            audio_state
        )
    except Exception as e:
        logger.error(f"Delete mistake error: {str(e)}")
        return (
            f"删除失败：{str(e)}，请稍后重试",
            get_mistakes_list(),
            get_stats_data()[0],
            None,
            None,
            gr.update(value=""),
            audio_state
        )

async def recommend_practice(mistake_id: str, tone: str = "温柔姐姐", answer_state=gr.State("")):
    """根据错题推荐练习题目并分离答案。"""
    try:
        mistake_id = int(mistake_id)
        if mistake_id <= 0:
            return (
                "错题 ID 必须为正整数",
                "",
                get_mistakes_list(),
                gr.update(value=""),
                gr.update(interactive=True, visible=True),
                gr.State(""),
                gr.State(False)
            )
        conn = next(get_db_connection())
        cursor = conn.cursor()
        cursor.execute("SELECT question, subject FROM mistakes WHERE id = ?", (mistake_id,))
        row = cursor.fetchone()
        if row:
            question, subject = row
            tone_prompt = {
                "严厉老师": "以严肃、权威的语气回答，逻辑清晰，注重细节，像一位严格的老师。",
                "温柔姐姐": "以亲切、温暖的语气回答，语言柔和，充满鼓励，像一位贴心的姐姐。",
                "搞笑同学": "以幽默、轻松的语气回答，加入适当的玩笑，像一位爱开玩笑的同学。"
            }[tone]
            prompt = (
                f"{tone_prompt}\n我在{subject}的{question}相关的题目上出错了，"
                "请给我出些题目并给出对应答案，答案放在最后，用固定的格式**以下是答案：**开始答案部分。"
                "回复时请使用纯文本，尽量不使用emoji。"
            )
            log_action(f"推荐练习: ID {mistake_id}, 科目: {subject}, 问题: {question}")
            response = chat_with_deepseek(prompt)
            chat_history.append(f"助手: {response}")

            answer_match = re.search(r"以下是答案.*", response, re.DOTALL)
            if answer_match:
                start_idx = answer_match.start()
                answers = response[start_idx:].strip()
                problem_text = response[:start_idx].rstrip()
            else:
                answers = ""
                problem_text = response

            return (
                problem_text,
                "",
                get_mistakes_list(),
                gr.update(value=""),
                gr.update(interactive=True, visible=True),
                gr.State(answers),
                gr.State(False)
            )
        return (
            f"未找到错题 ID {mistake_id}，请检查错题列表",
            "",
            get_mistakes_list(),
            gr.update(value=""),
            gr.update(interactive=True, visible=True),
            gr.State(""),
            gr.State(False)
        )
    except ValueError:
        return (
            "请输入有效的错题 ID（正整数）",
            "",
            get_mistakes_list(),
            gr.update(value=""),
            gr.update(interactive=True, visible=True),
            gr.State(""),
            gr.State(False)
        )
    except Exception as e:
        logger.error(f"Recommend practice error: {str(e)}")
        return (
            f"推荐练习失败：{str(e)}，请稍后重试",
            "",
            get_mistakes_list(),
            gr.update(value=""),
            gr.update(interactive=True, visible=True),
            gr.State(""),
            gr.State(False)
        )

def toggle_answer_visibility(is_visible: bool, answer: str):
    """切换答案显示状态。"""
    if is_visible:
        return "", False, "查看解析"
    return answer, True, "收起解析"

def get_stats_data():
    """生成错题分布统计图和数据。"""
    try:
        conn = next(get_db_connection())
        cursor = conn.cursor()
        cursor.execute("SELECT subject, COUNT(*) FROM mistakes GROUP BY subject")
        data = cursor.fetchall()
        total = sum(count for _, count in data)
        subjects = [row[0] for row in data] or ["无数据"]
        counts = [row[1] for row in data] or [1]
        percentages = [(count / total * 100) if total > 0 else 0 for count in counts]
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.pie(
            counts,
            labels=subjects,
            autopct="%1.1f%%",
            colors=["#FF6384", "#36A2EB", "#FFCE56", "#4BC0C0", "#9966FF", "#FF9F40", "#FFCD56", "#C9CBCF"]
        )
        ax.set_title("错题分布")
        return fig, subjects, percentages
    except Exception as e:
        logger.error(f"Get stats data error: {str(e)}")
        return None, [], []

def get_all_mistakes() -> str:
    """获取所有错题内容，按科目排序。"""
    try:
        conn = next(get_db_connection())
        cursor = conn.cursor()
        cursor.execute("SELECT subject, question FROM mistakes ORDER BY subject")
        rows = cursor.fetchall()
        if not rows:
            return "暂无错题记录"
        return "\n".join(f"{subject}: {question}" for subject, question in rows)
    except Exception as e:
        logger.error(f"Get all mistakes error: {str(e)}")
        return f"获取错题失败：{str(e)}"

async def analyze_learning() -> str:
    """分析学习情况，基于错题内容和科目分布。"""
    try:
        mistakes_text = get_all_mistakes()
        if "暂无错题" in mistakes_text or "获取错题失败" in mistakes_text:
            return "暂无错题数据，无法分析，请先记录错题"

        _, subjects, percentages = get_stats_data()
        if not subjects or not percentages:
            return "无法获取科目分布数据，请稍后重试"

        distribution_text = "\n".join(f"{subject}: {percentage:.1f}%" for subject, percentage in zip(subjects, percentages))

        prompt = (
            "你是一个学习分析助手，请根据以下数据分析学生的学习情况，提供简洁、准确的建议。\n"
            "1. 错题内容（按科目分组）：\n"
            f"{mistakes_text}\n\n"
            "2. 各科目错题占比：\n"
            f"{distribution_text}\n\n"
            "请分析学生的薄弱科目、常见错误类型，并给出针对性的学习建议。回复使用纯文本，尽量不使用emoji，字数控制在200字以内。"
        )

        log_action("请求学习情况分析")
        return chat_with_deepseek(prompt)
    except Exception as e:
        logger.error(f"Analyze learning error: {str(e)}")
        return f"分析失败：{str(e)}，请稍后重试"

async def handle_input(question: str, tone: str = "温柔姐姐"):
    """处理用户文本输入，支持作业和错题命令。"""
    if not question:
        return "请输入问题内容", None, get_history_list(), gr.update(value="")
    log_action(f"文字输入: {question}")
    chat_history.append(f"你: {question}")
    if "作业" in question:
        return await handle_homework_command(question)
    if "错题" in question:
        return await handle_mistake_command(question)
    try:
        tone_prompt = {
            "严厉老师": "以严肃、权威的语气回答，逻辑清晰，注重细节，像一位严格的老师。",
            "温柔姐姐": "以亲切、温暖的语气回答，语言柔和，充满鼓励，像一位贴心的姐姐。",
            "搞笑同学": "以幽默、轻松的语气回答，加入适当的玩笑，像一位爱开玩笑的同学。"
        }[tone]
        prompt = f"{tone_prompt}\n用户问题：{question}"
        response = chat_with_deepseek(prompt)
        chat_history.append(f"助手: {response}")
        audio_data = await generate_audio(response, tone)
        if not audio_data:
            response += "\n（语音回复生成失败，请检查日志或重试）"
        return response, audio_data, get_history_list(), gr.update(value="")
    except Exception as e:
        logger.error(f"DeepSeek error: {str(e)}")
        return (
            f"处理失败：{str(e)}，请检查网络或稍后重试",
            None,
            get_history_list(),
            gr.update(value="")
        )

async def handle_homework_command(text: str):
    """处理作业相关命令。"""
    if "添加" in text:
        task = text.replace("添加作业", "").strip()
        deadline = "2025-06-30"
        if validate_date_format(deadline):
            try:
                conn = next(get_db_connection())
                cursor = conn.cursor()
                cursor.execute("INSERT INTO homework (task, deadline) VALUES (?, ?)", (task, deadline))
                conn.commit()
                reset_ids(cursor, conn, "homework")
                log_action(f"添加作业: {task}")
                chat_history.append(f"助手: 已添加作业: {task}")
                audio_data = await generate_audio(f"已添加作业：{task}")
                if not audio_data:
                    return (
                        f"已添加作业：{task}\n（语音回复生成失败）",
                        None,
                        get_history_list(),
                        get_homework_list()
                    )
                return (
                    f"已添加作业：{task}",
                    audio_data,
                    get_history_list(),
                    get_homework_list()
                )
            except Exception as e:
                logger.error(f"Add homework error: {str(e)}")
                return (
                    f"添加作业失败：{str(e)}，请稍后重试",
                    None,
                    get_history_list(),
                    get_homework_list()
                )
        return (
            "截止日期无效（需不早于今天，格式为 YYYY-MM-DD）",
            None,
            get_history_list(),
            get_homework_list()
        )
    return (
        "请输入有效作业指令（如：添加作业 数学练习）",
        None,
        get_history_list(),
        get_homework_list()
    )

async def handle_mistake_command(text: str):
    """处理错题相关命令。"""
    if "记录" in text:
        question = text.replace("记录错题", "").strip()
        subject = "数学"
        try:
            conn = next(get_db_connection())
            cursor = conn.cursor()
            cursor.execute("INSERT INTO mistakes (question, subject) VALUES (?, ?)", (question, subject))
            conn.commit()
            reset_ids(cursor, conn, "mistakes")
            log_action(f"记录错题: {question}")
            chat_history.append(f"助手: 已记录错题: {question}")
            audio_data = await generate_audio(f"已记录错题：{question}")
            if not audio_data:
                return (
                    f"已记录错题：{question}\n（语音回复生成失败）",
                    None,
                    get_history_list(),
                    get_mistakes_list(),
                    get_stats_data()[0]
                )
            return (
                f"已记录错题：{question}",
                audio_data,
                get_history_list(),
                get_mistakes_list(),
                get_stats_data()[0]
            )
        except Exception as e:
            logger.error(f"Add mistake error: {str(e)}")
            return (
                f"记录错题失败：{str(e)}，请稍后重试",
                None,
                get_history_list(),
                get_mistakes_list(),
                get_stats_data()[0]
            )
    return (
        "请输入有效错题指令（如：记录错题 二次方程解错了）",
        None,
        get_history_list(),
        get_mistakes_list(),
        get_stats_data()[0]
    )

async def continuous_voice_chat(audio, history=None):
    """处理连续语音对话。"""
    if not audio:
        return history or [{
            "role": "system",
            "content": "你是一个作业辅导助手，提供准确、简洁的学术解答。回复时请使用纯文本，尽量不使用emoji。"
        }]
    try:
        input_type = "文件上传" if isinstance(audio, str) else "麦克风输入"
        audio_data = open(audio, "rb").read() if input_type == "文件上传" else audio
        user_input, _ = await process_voice_input(audio_data)
        if not user_input:
            return history or [{
                "role": "system",
                "content": "你是一个作业辅导助手，提供准确、简洁的学术解答。回复时请使用纯文本，尽量不使用emoji。"
            }]
        log_action(f"连续语音输入（{input_type}）: {user_input}")
        if history is None:
            history = [{
                "role": "system",
                "content": "你是一个作业辅导助手，提供准确、简洁的学术解答。回复时请使用纯文本，尽量不使用emoji。"
            }]
        if not any(m.get("role") == "user" and m.get("content") == user_input for m in history):
            history.append({"role": "user", "content": user_input})
            response = chat_with_deepseek(user_input, history)
            if not any(m.get("role") == "assistant" and m.get("content") == response for m in history):
                history.append({"role": "assistant", "content": response})
        return history
    except Exception as e:
        logger.error(f"Continuous voice chat error ({input_type}): {str(e)}")
        return history or [{
            "role": "system",
            "content": f"语音处理失败：{str(e)}，请确保音频格式正确（如 WAV、MP3）"
        }]

def clear_history():
    """清除对话历史。"""
    return [{
        "role": "system",
        "content": "你是一个作业辅导助手，提供准确、简洁的学术解答。回复时请使用纯文本，尽量不使用emoji。"
    }]

def get_history_list() -> str:
    """获取最近 10 条对话历史。"""
    return "\n".join(chat_history[-10:]) or "暂无对话"

# --------------------- Gradio 界面 ---------------------

def create_interface():
    with gr.Blocks(
        theme=gr.themes.Soft(),
        css="""
            .title-center h1 { text-align: center; }
            .sidebar-button {
                border: none;
                background-color: transparent;
                text-align: center;
                padding: 10px;
                width: 100%;
                font-weight: bold;
                cursor: pointer;
                border-left: 5px solid transparent;
                transition: all 0.2s;
            }
            .sidebar-button:hover { background-color: #e0e7ff; }
            .sidebar-button.selected {
                background-color: #c7d2fe;
                border-left: 5px solid #6366f1;
            }
        """
    ) as demo:
        answer_state = gr.State("")
        answer_visible_state = gr.State(False)

        with gr.Column(elem_classes=["title-center"]):
            gr.Markdown("# 作业辅导系统")

        with gr.Row():
            with gr.Sidebar():
                btn_chat = gr.Button("文字对话", elem_classes=["sidebar-button", "selected"])
                btn_homework = gr.Button("作业管理", elem_classes=["sidebar-button"])
                btn_mistakes = gr.Button("错题记录", elem_classes=["sidebar-button"])
                btn_stats = gr.Button("学习统计与分析", elem_classes=["sidebar-button"])
                btn_voice = gr.Button("连续语音对话", elem_classes=["sidebar-button"])

            with gr.Column(scale=4):
                with gr.Column(visible=True) as tab_chat:
                    gr.Markdown("## 文字对话")
                    tone_selector = gr.Dropdown(
                        ["严厉老师", "温柔姐姐", "搞笑同学"], label="助教语气", value="温柔姐姐"
                    )
                    question_input = gr.Textbox(
                        label="输入问题", placeholder="请输入问题（如：什么是牛顿第二定律）"
                    )
                    with gr.Row():
                        submit_button = gr.Button("提交", variant="primary")
                        clear_button = gr.Button("清空")
                    response_output = gr.Textbox(label="提示", interactive=False)
                    audio_output = gr.Audio(label="语音回复", autoplay=False, interactive=False)
                    history_output = gr.Textbox(label="对话历史", interactive=False, lines=8)

                with gr.Column(visible=False) as tab_homework:
                    gr.Markdown("## 作业管理")
                    with gr.Row():
                        homework_input = gr.Textbox(
                            label="作业内容", placeholder="如：完成数学练习"
                        )
                        deadline_input = gr.DateTime(
                            label="截止日期", include_time=False, type="string", value=None
                        )
                    add_homework_button = gr.Button("添加作业", variant="primary")
                    with gr.Row():
                        homework_id_input = gr.Textbox(
                            label="作业 ID", placeholder="如：1"
                        )
                        with gr.Column():
                            delete_homework_button = gr.Button("删除作业", variant="secondary")
                            refresh_button = gr.Button("刷新", variant="secondary")
                    response_output_homework = gr.Textbox(label="提示", interactive=False)
                    homework_list = gr.Textbox(label="作业列表", interactive=False, lines=6)

                with gr.Column(visible=False) as tab_mistakes:
                    gr.Markdown("## 错题记录")
                    with gr.Row():
                        mistake_input = gr.Textbox(
                            label="错题内容", placeholder="如：二次方程解错了"
                        )
                        subject_input = gr.Dropdown(
                            ["数学", "物理", "英语", "语文", "化学", "地理", "生物", "其他"],
                            label="科目",
                            value="数学"
                        )
                    mistake_image_input = gr.Image(label="上传图片（可选）", type="filepath")
                    add_mistake_button = gr.Button("记录错题", variant="primary")
                    with gr.Row():
                        subject_filter = gr.Dropdown(
                            ["全部", "数学", "物理", "英语", "语文", "化学", "地理", "生物", "其他"],
                            label="筛选科目",
                            value="全部"
                        )
                        filter_mistakes_button = gr.Button("筛选错题", variant="secondary")
                    mistakes_list = gr.Textbox(label="错题列表", interactive=False, lines=6)
                    with gr.Row():
                        mistake_id_input = gr.Textbox(label="错题 ID", placeholder="如：1")
                        with gr.Column():
                            view_mistake_button = gr.Button("查看错题", variant="secondary")
                            delete_mistake_button = gr.Button("删除错题", variant="secondary")
                        with gr.Column():
                            recommend_button = gr.Button("题目推荐", variant="secondary")
                            show_answer_button = gr.Button("查看解析", variant="secondary")
                    with gr.Row():
                        response_output_mistakes = gr.Textbox(label="回复提示", interactive=False)
                        answer_output = gr.Textbox(label="解析", interactive=False, value="")
                    mistake_details = gr.Textbox(label="错题详情")
                    mistake_image_output = gr.Image(label="错题图片")

                with gr.Column(visible=False) as tab_stats:
                    gr.Markdown("## 学习统计")
                    response_output_stats = gr.Textbox(label="提示", interactive=False)
                    stats_plot = gr.Plot(label="错题分布图")
                    gr.Markdown("## 学习分析")
                    gr.Markdown("点击下方按钮，基于错题内容和科目分布分析学习情况")
                    analyze_button = gr.Button("学习分析", variant="primary")
                    analysis_output = gr.Textbox(label="分析结果", interactive=False, lines=6)

                with gr.Column(visible=False) as tab_voice:
                    gr.Markdown("## 连续语音对话")
                    gr.Markdown("### 提示：点击麦克风图标录制音频，或点击上传按钮选择音频文件（支持 WAV、MP3 等格式）")
                    voice_input = gr.Audio(label="语音输入（录制或上传）", interactive=True)
                    start_button = gr.Button("提交", variant="primary")
                    chatbot_output = gr.Chatbot(label="对话", value=[], height=400, type="messages")
                    clear_history_button = gr.Button("清除记录", variant="secondary")

        # 切换选项卡
        def set_active_tab(tab: str):
            classes = {
                "chat": ["selected", "", "", "", ""],
                "homework": ["", "selected", "", "", ""],
                "mistakes": ["", "", "selected", "", ""],
                "stats": ["", "", "", "selected", ""],
                "voice": ["", "", "", "", "selected"]
            }[tab]
            outputs = {
                tab_chat: gr.update(visible=(tab == "chat")),
                tab_homework: gr.update(visible=(tab == "homework")),
                tab_mistakes: gr.update(visible=(tab == "mistakes")),
                tab_stats: gr.update(visible=(tab == "stats")),
                tab_voice: gr.update(visible=(tab == "voice")),
                btn_chat: gr.update(elem_classes=["sidebar-button", classes[0]]),
                btn_homework: gr.update(elem_classes=["sidebar-button", classes[1]]),
                btn_mistakes: gr.update(elem_classes=["sidebar-button", classes[2]]),
                btn_stats: gr.update(elem_classes=["sidebar-button", classes[3]]),
                btn_voice: gr.update(elem_classes=["sidebar-button", classes[4]])
            }
            if tab == "stats":
                outputs[stats_plot] = gr.update(value=get_stats_data()[0])
            return outputs

        # 绑定事件
        btn_chat.click(
            fn=lambda: set_active_tab("chat"),
            outputs=[tab_chat, tab_homework, tab_mistakes, tab_stats, tab_voice, btn_chat, btn_homework, btn_mistakes, btn_stats, btn_voice]
        )
        btn_homework.click(
            fn=lambda: set_active_tab("homework"),
            outputs=[tab_chat, tab_homework, tab_mistakes, tab_stats, tab_voice, btn_chat, btn_homework, btn_mistakes, btn_stats, btn_voice]
        )
        btn_mistakes.click(
            fn=lambda: set_active_tab("mistakes"),
            outputs=[tab_chat, tab_homework, tab_mistakes, tab_stats, tab_voice, btn_chat, btn_homework, btn_mistakes, btn_stats, btn_voice]
        )
        btn_stats.click(
            fn=lambda: set_active_tab("stats"),
            outputs=[tab_chat, tab_homework, tab_mistakes, tab_stats, tab_voice, btn_chat, btn_homework, btn_mistakes, btn_stats, btn_voice, stats_plot]
        )
        btn_voice.click(
            fn=lambda: set_active_tab("voice"),
            outputs=[tab_chat, tab_homework, tab_mistakes, tab_stats, tab_voice, btn_chat, btn_homework, btn_mistakes, btn_stats, btn_voice]
        )

        submit_button.click(
            fn=handle_input,
            inputs=[question_input, tone_selector],
            outputs=[response_output, audio_output, history_output, question_input]
        ).then(
            fn=play_audio,
            inputs=[audio_output],
            outputs=[]
        )
        clear_button.click(
            fn=lambda: ("", []),
            outputs=[question_input, history_output]
        )
        clear_history_button.click(
            fn=clear_history,
            outputs=[chatbot_output]
        )
        add_homework_button.click(
            fn=add_homework,
            inputs=[homework_input, deadline_input, gr.State(None)],
            outputs=[response_output_homework, homework_list, homework_input, deadline_input, gr.State()]
        ).then(
            fn=play_audio,
            inputs=[gr.State()],
            outputs=[audio_output]
        )
        delete_homework_button.click(
            fn=delete_homework,
            inputs=[homework_id_input, gr.State(None)],
            outputs=[response_output_homework, homework_list, homework_id_input, gr.State()]
        ).then(
            fn=play_audio,
            inputs=[gr.State()],
            outputs=[audio_output]
        )
        refresh_button.click(
            fn=refresh_homework,
            inputs=[],
            outputs=[response_output_homework, homework_list]
        )
        add_mistake_button.click(
            fn=add_mistake,
            inputs=[mistake_input, subject_input, mistake_image_input, gr.State(None)],
            outputs=[response_output_mistakes, mistakes_list, stats_plot, mistake_details, mistake_image_output, mistake_input, mistake_image_input, gr.State()]
        ).then(
            fn=play_audio,
            inputs=[gr.State()],
            outputs=[audio_output]
        )
        filter_mistakes_button.click(
            fn=get_mistakes_list,
            inputs=subject_filter,
            outputs=mistakes_list
        )
        view_mistake_button.click(
            fn=get_mistake_details,
            inputs=mistake_id_input,
            outputs=[mistake_details, mistake_image_output, mistake_id_input, mistake_image_input]
        )
        delete_mistake_button.click(
            fn=delete_mistake,
            inputs=[mistake_id_input, gr.State(None)],
            outputs=[response_output_mistakes, mistakes_list, stats_plot, mistake_details, mistake_image_output, mistake_id_input, gr.State()]
        ).then(
            fn=play_audio,
            inputs=[gr.State()],
            outputs=[audio_output]
        )
        recommend_button.click(
            fn=recommend_practice,
            inputs=[mistake_id_input, tone_selector, answer_state],
            outputs=[response_output_mistakes, answer_output, mistakes_list, mistake_id_input, show_answer_button, answer_state, answer_visible_state]
        )
        show_answer_button.click(
            fn=toggle_answer_visibility,
            inputs=[answer_visible_state, answer_state],
            outputs=[answer_output, answer_visible_state, show_answer_button]
        )
        start_button.click(
            fn=continuous_voice_chat,
            inputs=[voice_input, chatbot_output],
            outputs=[chatbot_output]
        )
        clear_history_button.click(
            fn=clear_history,
            outputs=[chatbot_output]
        )
        analyze_button.click(
            fn=analyze_learning,
            inputs=[],
            outputs=[analysis_output]
        )

        # 初始显示内容
        homework_list.value = get_homework_list()
        mistakes_list.value = get_mistakes_list()
        stats_plot.value = get_stats_data()[0]

    return demo

if __name__ == "__main__":
    demo = create_interface()
    demo.launch(server_port=7861)