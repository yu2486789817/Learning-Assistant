# Learning-Assistant

## 项目简介
Learning Assistant 是一个智能作业辅导系统，帮助学生管理作业、记录错题、分析学习情况，并支持语音与文字交互。基于 Python 和 Gradio 开发，提供直观的 Web 界面，适合学生使用。

## 主要功能
- **文字对话**：选择“严厉老师”等语气，解答问题，如“什么是牛顿第二定律？”。
- **作业管理**：添加（如“数学练习”）、删除、查看作业。
- **错题记录**：记录错题（如“二次方程解错了”），支持图片上传。
- **学习分析**：生成错题分布图，建议学习重点。
- **语音交互**：通过麦克风或音频文件进行多轮对话。

## 安装
1. 确保 Python 3.8+ 已安装。
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
   或直接：
   ```bash
   pip install gradio==5.33.1 matplotlib==3.10.3 pillow==11.2.0 pygame==2.6.1 edge-tts==7.0.0 speechrecognition==3.14.3 pyaudio==0.2.14 openai==1.70.0
   ```

## 运行
```bash
python app.py
```
访问 `http://127.0.0.1:7861` 查看 Gradio 界面。

## 文档
详见 [learning_assistant_documentation_en.docx]。

## 许可证
MIT License
