from NewsPilot.core.frequency import load_frequency_words, explain_word_group_match
groups, filters, globals_ = load_frequency_words()
print(explain_word_group_match("让模型自己找关键帧、视觉线索，小红书Vedio-Thinker破解视频推理困局", groups, filters, globals_))