from app.punctuate import restore


def test_marks_are_placed_inside_segments_and_at_their_ends():
    texts = ["今天的會議由詹翊甯主持 邱瀞瑩負責記錄", "第一個議題是 Kubernetes"]
    punctuated = "今天的會議由詹翊甯主持，邱瀞瑩負責記錄。第一個議題是Kubernetes。"
    assert restore(texts, punctuated) == ["今天的會議由詹翊甯主持，邱瀞瑩負責記錄。", "第一個議題是 Kubernetes。"]


def test_spaces_between_chinese_and_english_are_kept():
    texts = ["那顯然這樣子的 attention 是有問題的你在做語音合成的時候"]
    punctuated = "那顯然這樣子的attention是有問題的，你在做語音合成的時候。"
    assert restore(texts, punctuated) == ["那顯然這樣子的 attention 是有問題的，你在做語音合成的時候。"]


def test_existing_marks_take_the_model_choice_or_stay():
    assert restore(["主持,邱瀞瑩"], "主持，邱瀞瑩。") == ["主持，邱瀞瑩。"]
    assert restore(["主持,邱瀞瑩"], "主持邱瀞瑩") == ["主持,邱瀞瑩"]
    assert restore(["主持,邱瀞瑩"], "主持。,邱瀞瑩。") == ["主持。邱瀞瑩。"]


def test_leading_marks_are_dropped():
    assert restore(["你好"], "。你好？") == ["你好？"]


def test_case_changes_keep_the_original_letters():
    assert restore(["line 官方帳號"], "LINE官方帳號。") == ["line 官方帳號。"]


def test_changed_text_is_rejected():
    assert restore(["你好"], "您好。") is None
    assert restore(["你好"], "你好嗎") is None
