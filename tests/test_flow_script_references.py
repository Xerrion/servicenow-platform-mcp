"""Authoring references are discovered lexically, never by running JavaScript."""

import pytest

from servicenow_mcp.capabilities.flows.script_refs import (
    ScriptReference,
    references,
    rewrite,
    rewrite_mirror,
)


def test_references_return_complete_exact_source_spans_and_named_paths():
    code = "return fd_data . _1__lookup /* preserve */ . record.sys_id + fd_data.trigger.current;"
    first = "fd_data . _1__lookup /* preserve */ . record.sys_id"
    second = "fd_data.trigger.current"
    start = code.index(first)
    second_start = code.index(second)
    assert references(code) == [
        ScriptReference(start, start + len(first), "_1__lookup", ("record", "sys_id")),
        ScriptReference(
            second_start, second_start + len(second), "trigger", ("current",)
        ),
    ]
    assert [code[ref.start : ref.end] for ref in references(code)] == [first, second]


def test_swapped_producers_rewrite_simultaneously_without_cascading():
    code = "return fd_data._1__one.output + fd_data._2__two.output + fd_data._1__one.output;"
    assert rewrite(code, {"_1__one": "_2__two", "_2__two": "_1__one"}) == (
        "return fd_data._2__two.output + fd_data._1__one.output + fd_data._2__two.output;"
    )


def test_producer_rewrite_has_no_prefix_collisions_or_suffix_changes():
    code = "[fd_data._1.value, fd_data._10.value, fd_data._1__long.value, fd_data.trigger._1]"
    assert rewrite(code, {"_1": "_10", "_10": "_1"}) == (
        "[fd_data._10.value, fd_data._1.value, fd_data._1__long.value, fd_data.trigger._1]"
    )


def test_rewrite_preserves_unmapped_producers_and_all_unrelated_characters():
    code = (
        "/* fd_data._1__one.value */\r\n"
        "return fd_data\t. /* producer */ _1__one /* suffix */ .value + "
        '"fd_data._1__one.value café" + fd_data.flow_var.counter;'
    )
    assert rewrite(code, {"_1__one": "_5__one"}) == (
        "/* fd_data._1__one.value */\r\n"
        "return fd_data\t. /* producer */ _5__one /* suffix */ .value + "
        '"fd_data._1__one.value café" + fd_data.flow_var.counter;'
    )
    assert rewrite(code, {}) == code


def test_comment_string_and_regexp_contents_are_not_executable_references():
    code = r"""
        // fd_data._1__one.value
        /* fd_data._1__one.value */
        "fd_data._1__one.value \\\" escaped";
        'fd_data._1__one.value \\\' escaped';
        /fd_data\._1__one\.value/gi;
        /[/'"]fd_data\._1__one\.value/.test(value);
        if ((ready)) /fd_data._1__one.value/.test(value);
        for await (const item of values) /fd_data._1__one.value/.test(item);
        return fd_data._1__one.value;
    """
    assert [reference.producer for reference in references(code)] == ["_1__one"]
    assert rewrite(code, {"_1__one": "_9__one"}) == code.replace(
        "return fd_data._1__one.value;", "return fd_data._9__one.value;"
    )


@pytest.mark.parametrize(
    "code",
    [
        "object.fd_data.trigger.current;",
        "object?.fd_data.trigger.current;",
        "my_fd_data.trigger.current;",
        "fd_data_extra.trigger.current;",
        "αfd_data.trigger.current;",
        "fd_data\u200c.trigger.current;",
        "const object = {fd_data: 42};",
        "fd_data: while (false) { break; }",
        "1..fd_data.trigger.current;",
    ],
)
def test_only_a_root_identifier_is_a_reference(code: str):
    assert references(code) == []
    assert rewrite(code, {"trigger": "_1__one"}) == code


@pytest.mark.parametrize(
    "code",
    [
        "return 10 / fd_data._1__one.value / 2;",
        "object.if(value) / fd_data._1__one.value / 2;",
        "object.return / fd_data._1__one.value / 2;",
        "continue\nlabel / fd_data._1__one.value / 2;",
    ],
)
def test_division_does_not_hide_executable_references(code):
    assert [reference.producer for reference in references(code)] == ["_1__one"]
    assert rewrite(code, {"_1__one": "_2__one"}) == code.replace(
        "fd_data._1__one.value", "fd_data._2__one.value"
    )


@pytest.mark.parametrize(
    "prefix", ["break\n", "continue\n", "debugger\n", "break label\n"]
)
def test_regexp_statements_after_nonexpression_keywords_are_ignored(prefix):
    code = prefix + "/fd_data._1__one.value/g;"
    assert references(code) == []
    assert rewrite(code, {"_1__one": "_2__one"}) == code


def test_template_raw_text_is_ignored_and_interpolations_are_executable():
    code = r"""`raw fd_data._1__one.value \${fd_data._1__one.value} ${fd_data._1__one.value} ${`nested ${fd_data.trigger.current.sys_id}`} end`;"""
    assert [(reference.producer, reference.path) for reference in references(code)] == [
        ("_1__one", ("value",)),
        ("trigger", ("current", "sys_id")),
    ]
    assert rewrite(code, {"_1__one": "_5__one", "trigger": "_6__trigger"}) == (
        r"""`raw fd_data._1__one.value \${fd_data._1__one.value} ${fd_data._5__one.value} ${`nested ${fd_data._6__trigger.current.sys_id}`} end`;"""
    )


def test_interpolation_delimiters_inside_literals_comments_and_objects_are_safe():
    code = """`raw ${ /}/.test("}") ? ({key: fd_data._1__one.value}) : /* } */ `inner ${fd_data.flow_var.count}` }`;"""
    assert [(reference.producer, reference.path) for reference in references(code)] == [
        ("_1__one", ("value",)),
        ("flow_var", ("count",)),
    ]
    assert rewrite(code, {"_1__one": "_2__one"}) == code.replace(
        "fd_data._1__one.value", "fd_data._2__one.value"
    )


def test_producer_only_reference_is_left_for_caller_scope_resolution():
    code = "[fd_data.trigger, fd_data.flow_var, fd_data.subflow_inputs]"
    assert [(reference.producer, reference.path) for reference in references(code)] == [
        ("trigger", ()),
        ("flow_var", ()),
        ("subflow_inputs", ()),
    ]


def test_grouping_and_call_results_do_not_extend_direct_reference_spans():
    code = "[use((fd_data.trigger.current))[field], (fd_data.flow_var.count)]"
    assert [code[ref.start : ref.end] for ref in references(code)] == [
        "fd_data.trigger.current",
        "fd_data.flow_var.count",
    ]
    assert rewrite(code, {"trigger": "_1__trigger"}) == (
        "[use((fd_data._1__trigger.current))[field], (fd_data.flow_var.count)]"
    )


@pytest.mark.parametrize(
    "code",
    [
        "fd_data['trigger'].current",
        'fd_data["trigger"].current',
        "fd_data[producer].value",
        "fd_data.trigger[field]",
        "fd_data.trigger.current['sys_id']",
        "fd_data?.trigger.current",
        "fd_data.trigger?.current",
        "fd_data.trigger.current?.sys_id",
        "fd_data.trigger?.[field]",
        "const alias = fd_data; alias.trigger.current;",
        "const alias = ready ? fd_data : other;",
        "switch (value) { case fd_data: break; }",
        "const alias = {key: fd_data};",
        "use(fd_data);",
        "(fd_data).trigger.current",
        "(fd_data.trigger.current)[field]",
        "((fd_data.trigger.current))?.sys_id",
        "(fd_data.trigger.current).sys_id",
        "const {trigger} = fd_data;",
        "fd_data = other;",
        "return fd_data;",
        "`raw ${fd_data[producer].value}`",
    ],
)
def test_dynamic_optional_and_aliased_root_access_fails_closed(code):
    with pytest.raises(ValueError, match="fd_data"):
        references(code)
    with pytest.raises(ValueError, match="fd_data"):
        rewrite(code, {"trigger": "_9__trigger"})


@pytest.mark.parametrize(
    "declaration",
    [
        "var fd_data = other;",
        "let fd_data;",
        "const fd_data = other;",
        "function f(fd_data) {}",
        "function fd_data() {}",
        "const f = fd_data => fd_data.trigger.current;",
        "const f = (fd_data = other) => fd_data.trigger.current;",
        "function f(...fd_data) {}",
        "const {fd_data} = object;",
        "const {key: fd_data} = object;",
        "try {} catch (fd_data) {}",
        "class fd_data {}",
    ],
)
def test_detectable_shadow_declarations_are_rejected_not_silently_ignored(declaration):
    code = declaration + "\nfd_data.trigger.current;"
    with pytest.raises(ValueError, match="fd_data"):
        references(code)
    with pytest.raises(ValueError, match="fd_data"):
        rewrite(code, {"trigger": "_2__trigger"})


@pytest.mark.parametrize(
    "malformed",
    [
        "fd_data.",
        "fd_data..trigger.current",
        "fd_data.trigger.",
        "fd_data.trigger..current",
        "fd_data.1.value",
        "fd_data.trigger...current",
        "fd_data.trigger.café",
        "fd_data /* comment */ trigger.current",
    ],
)
def test_malformed_direct_references_are_never_partially_rewritten(malformed):
    code = "fd_data._1__one.value; " + malformed
    with pytest.raises(ValueError):
        references(code)
    with pytest.raises(ValueError):
        rewrite(code, {"_1__one": "_9__one", "trigger": "_8__trigger"})


@pytest.mark.parametrize(
    "code",
    [
        r"fd_\u0064ata.trigger.current",
        r"fd_data.tri\u0067ger.current",
        "if (ready) {} /fd_data.trigger.current/;",
        "value = of / fd_data.trigger.current / 2;",
        "yield /fd_data.trigger.current/;",
        "await /fd_data.trigger.current/;",
        "const value = <node>fd_data.trigger.current</node>;",
        "<!-- fd_data.trigger.current -->",
        "/[[a]fd_data.trigger.current]/;",
        "/fd_data.trigger.current/v;",
        r"/[\q{]fd_data.trigger.current}]/v;",
        "with (other) { fd_data.trigger.current; }",
        "class C { #fd_data; } fd_data.trigger.current;",
    ],
)
def test_unsupported_ambiguous_lexical_syntax_fails_closed(code):
    with pytest.raises(ValueError):
        references(code)
    with pytest.raises(ValueError):
        rewrite(code, {"trigger": "_1__trigger"})


@pytest.mark.parametrize(
    "unfinished",
    [
        '"fd_data.trigger.current',
        "'fd_data.trigger.current",
        "/* fd_data.trigger.current",
        "/fd_data.trigger.current",
        "/[fd_data.trigger.current/",
        "`fd_data.trigger.current",
        "`raw ${fd_data.trigger.current",
        "(fd_data.trigger.current",
        "fd_data.trigger.current]",
    ],
)
def test_unterminated_literals_or_delimiters_do_not_return_partial_references(
    unfinished,
):
    with pytest.raises(ValueError):
        references(unfinished)
    with pytest.raises(ValueError):
        rewrite(unfinished, {"trigger": "_1__trigger"})


def test_source_and_reference_count_bounds_are_inclusive():
    assert references(" " * 65_000) == []
    assert rewrite(" " * 65_000, {}) == " " * 65_000
    with pytest.raises(ValueError, match="65000"):
        references(" " * 65_001)
    hundred = ";".join("fd_data.trigger.current" for _ in range(100))
    assert len(references(hundred)) == 100
    with pytest.raises(ValueError, match="100 fd_data references"):
        references(hundred + ";fd_data.trigger.current")
    with pytest.raises(ValueError, match="100 fd_data references"):
        rewrite(hundred + ";fd_data.trigger.current", {"trigger": "_1__trigger"})


def test_rewrite_cannot_exceed_source_bound():
    expression = "fd_data.x.value;"
    code = expression + " " * (65_000 - len(expression))
    with pytest.raises(ValueError, match="65000"):
        rewrite(code, {"x": "longer"})


@pytest.mark.parametrize(
    "replacement", ["", "a.b", "a[0]", "a?.b", "two words", "1bad", "café"]
)
def test_replacement_values_cannot_inject_or_break_member_syntax(replacement):
    with pytest.raises(ValueError, match="named ASCII identifier"):
        rewrite("fd_data.trigger.current", {"trigger": replacement})


def test_template_interpolation_nesting_has_a_conservative_bound():
    code = "fd_data.trigger.current"
    for _ in range(32):
        code = "`${" + code + "}`"
    assert [reference.producer for reference in references(code)] == ["trigger"]
    with pytest.raises(ValueError, match="32 nested template interpolations"):
        references("`${" + code + "}`")


def test_mirror_rewrites_only_entry_script_producers_and_preserves_json_bytes():
    mirror = r""" {
  "log": { "script": "return fd_data.\u005f1__one.value + \"fd_data._1__one.value\";\n", "scriptActive": true,
    "showScript": false, "opaque": "fd_data[unknown]", "metadata": {"script": "fd_data._1__one.value"} },
  "other": {"showScript":true, "script":"return fd_data._2__two.value;", "encoded":"\u005f1__one"}
} """
    expected = r""" {
  "log": { "script": "return fd_data._2__two.value + \"fd_data._1__one.value\";\n", "scriptActive": true,
    "showScript": false, "opaque": "fd_data[unknown]", "metadata": {"script": "fd_data._1__one.value"} },
  "other": {"showScript":true, "script":"return fd_data._1__one.value;", "encoded":"\u005f1__one"}
} """
    assert (
        rewrite_mirror(mirror, {"_1__one": "_2__two", "_2__two": "_1__one"}) == expected
    )


def test_mirror_maps_escaped_producer_offsets_after_surrogate_pairs():
    mirror = r"""{"f\u0069eld":{"\u0073cript":"\"café \ud83d\ude80\";\r\nreturn f\u0064_data.\u005f\u0031.value;\n"}}"""
    expected = r"""{"f\u0069eld":{"\u0073cript":"\"café \ud83d\ude80\";\r\nreturn f\u0064_data._8.value;\n"}}"""
    assert rewrite_mirror(mirror, {"_1": "_8"}) == expected


def test_mirror_preserves_unrelated_code_escape_spelling():
    mirror = r"""{"field":{"script":"var s=\"\\u0066d_data._1.value\"; return fd_data._1.value;","text":"fd_data._1.value"}}"""
    expected = r"""{"field":{"script":"var s=\"\\u0066d_data._1.value\"; return fd_data._2.value;","text":"fd_data._1.value"}}"""
    assert rewrite_mirror(mirror, {"_1": "_2"}) == expected


def test_mirror_leaves_unmapped_or_same_producer_encodings_exactly_unchanged():
    mirror = r""" { "field" : {"script":"return fd_data.\u005f1.value;", "meta":"fd_data._1.value"} } """
    assert rewrite_mirror(mirror, {}) == mirror
    assert rewrite_mirror(mirror, {"_1": "_1"}) == mirror
    assert rewrite_mirror(mirror, {"_2": "_3"}) == mirror
    assert rewrite_mirror(" \n{ }\t", {}) == " \n{ }\t"


@pytest.mark.parametrize(
    "mirror",
    [
        "[]",
        "null",
        '"opaque"',
        '{"field": 1}',
        '{"field": null}',
        '{"field": []}',
        '{"field": {}}',
        '{"field": {"script": null}}',
        '{"field": {"script": 7}}',
        '{"field": {"script": false}}',
        '{"field": {"script": []}}',
        '{"field": {"script": "return 1;"}} trailing',
        '{"field": {"script": "unterminated}}',
        '{"field": {"script": "return 1;", "script": "return 2;"}}',
        '{"field": {"script": "return 1;"}, "field": {"script": "return 2;"}}',
        '{"field": {"script": "return 1;", "metadata": NaN}}',
        '{"field": {"script": "return 1;", "metadata": Infinity}}',
    ],
)
def test_mirror_rejects_malformed_or_ambiguous_script_map_json(mirror: str):
    with pytest.raises((TypeError, ValueError)):
        rewrite_mirror(mirror, {"_1": "_2"})


def test_mirror_applies_the_same_script_reference_rejections():
    mirror = r"""{"field":{"script":"return fd_data.trigger[field];", "opaque":"fd_data._1.value"}}"""
    with pytest.raises(ValueError, match="fd_data"):
        rewrite_mirror(mirror, {"trigger": "_2__trigger"})


def test_mirror_template_interpolation_is_rewritten_but_raw_template_text_is_not():
    mirror = (
        r"""{"field":{"script":"return `raw fd_data._1.value ${fd_data._1.value}`;"}}"""
    )
    expected = (
        r"""{"field":{"script":"return `raw fd_data._1.value ${fd_data._2.value}`;"}}"""
    )
    assert rewrite_mirror(mirror, {"_1": "_2"}) == expected


def test_mirror_per_entry_remaps_pin_identical_tokens_to_different_producers():
    mirror = r""" {
 "first": {"script":"return fd_data._1__log.log_message;", "opaque":"fd_data._1__log.log_message"},
 "s\u0065cond": {"script":"return fd_data.\u005f1__log.log_message;", "meta":{"script":"fd_data._1__log.log_message"}},
 "untouched": {"script":"return fd_data._1__log.log_message;"}
} """
    expected = r""" {
 "first": {"script":"return fd_data._2__log.log_message;", "opaque":"fd_data._1__log.log_message"},
 "s\u0065cond": {"script":"return fd_data._3__log.log_message;", "meta":{"script":"fd_data._1__log.log_message"}},
 "untouched": {"script":"return fd_data._1__log.log_message;"}
} """
    assert (
        rewrite_mirror(
            mirror,
            {"_1__log": "_99__log"},
            entry_replacements={
                "first": {"_1__log": "_2__log"},
                "second": {"_1__log": "_3__log"},
            },
        )
        == expected
    )


def test_empty_entry_remaps_do_not_fall_back_to_common_producer_mapping():
    mirror = r"""{"field":{"script":"return fd_data.\u005f1__log.log_message;"}}"""
    assert (
        rewrite_mirror(mirror, {"_1__log": "_2__log"}, entry_replacements={}) == mirror
    )
