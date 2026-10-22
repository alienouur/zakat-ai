from chatbot.knowledge import EMPTY_BLOCK, Knowledge, citations_for, is_follow_up


def test_sheep_nisab_hits_table_record(index):
    ids = [h.record.id for h in index.search("كم نصاب الغنم")]
    assert "lzf_sheep_table" in ids[:3], ids


def test_debtor_question_hits_gharimin(index):
    ids = [h.record.id for h in index.search("علي دين لا أستطيع سداده")]
    assert "lzf_gharimin_debtors" in ids[:3], ids


def test_only_fund_dataset_is_used(index):
    for h in index.search("ما نصاب الذهب"):
        assert h.record.clone == "libya_zakat_fund"


def test_block_format(index):
    kn = index.build("كم نصاب الغنم")
    assert kn.block.startswith("<knowledge>\n<record id=\"") and kn.block.endswith("</record>\n</knowledge>")
    assert 'article="المادة' in kn.block and 'page="' in kn.block
    assert "السؤال:" in kn.block and "الحكم:" in kn.block and "الاقتباس الحرفي: «" in kn.block
    assert kn.citations and set(kn.citations[0]) == {"article", "page"}


def test_empty_case(index):
    kn = index.build("xyzzy qwerty 12345")
    assert kn.block == EMPTY_BLOCK and kn.hits == [] and kn.citations == []


def test_follow_up_uses_previous_question(index):
    assert is_follow_up("وكم الحد الأقصى؟") and not is_follow_up("ما المستندات المطلوبة لطلب إعانة من صندوق الزكاة للفقير")
    kn = index.build("وكم الحد الأقصى؟", ["ما إعانة العلاج للمريض العاجز"])
    assert "العلاج" in kn.query or "المريض" in kn.query
    assert kn.hits


def test_citations_filtered_by_reply(index):
    hits = index.search("الغارمون")
    all_c = citations_for(hits)
    art = all_c[0]["article"]
    num = [t for t in art.replace("(", " ").replace(")", " ").split() if t.isdigit()][0]
    picked = citations_for(hits, f"بحسب (المادة {num}، ص {all_c[0]['page']}) ...")
    assert picked and all(f"({num})" in c["article"] for c in picked)
    assert citations_for(hits, "لا توجد إحالة") == []
