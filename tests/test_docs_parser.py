from src.docs_parser import DocSection, chunk_markdown, matches_globs, replace_section


class TestChunkMarkdown:
    def test_preamble_then_headings(self):
        text = "Intro line\n\n# Title\nBody under title.\n"
        sections = chunk_markdown(text)
        assert sections == [
            DocSection(title="", level=0, content="Intro line"),
            DocSection(title="Title", level=1, content="Body under title."),
        ]

    def test_nested_headings_split(self):
        text = "## A\nA body\n### A.1\nSub body\n## B\nB body\n"
        sections = chunk_markdown(text)
        assert [s.title for s in sections] == ["A", "A.1", "B"]
        assert [s.level for s in sections] == [2, 3, 2]
        assert sections[1].content == "Sub body"

    def test_heading_levels_up_to_six(self):
        text = "# H1\nx\n###### H6\ny\n####### H7\nz\n"
        sections = chunk_markdown(text)
        assert [s.title for s in sections] == ["H1", "H6"]

    def test_no_headings_returns_single_section(self):
        text = "Just text\nmore text"
        sections = chunk_markdown(text)
        assert sections == [DocSection(title="", level=0, content="Just text\nmore text")]

    def test_empty_text(self):
        assert chunk_markdown("") == []
        assert chunk_markdown("\n\n") == []

    def test_headings_inside_code_fence_ignored(self):
        text = "# Real\nbefore\n```python\n# Not a heading\ndef f():\n    pass\n```\nafter\n"
        sections = chunk_markdown(text)
        assert [s.title for s in sections] == ["Real"]
        assert "# Not a heading" in sections[0].content

    def test_trailing_blank_lines_stripped(self):
        text = "# T\ncontent\n\n\n"
        sections = chunk_markdown(text)
        assert sections == [DocSection(title="T", level=1, content="content")]


class TestMatchesGlobs:
    def test_readme(self):
        assert matches_globs("README.md", "README.md,docs/**/*.md")
        assert not matches_globs("readme.md", "README.md,docs/**/*.md")

    def test_nested_docs(self):
        assert matches_globs("docs/guide/setup.md", "docs/**/*.md")
        assert matches_globs("docs/api.md", "docs/*.md,docs/**/*.md")

    def test_code_files_excluded(self):
        assert not matches_globs("src/main.py", "README.md,docs/**/*.md")

    def test_whitespace_in_patterns(self):
        assert matches_globs("docs/a.md", " docs/*.md , README.md ")


class TestReplaceSection:
    TEXT = "# Title\n\nintro\n\n## A\nA body\n\n## B\nB body\n"

    def test_replaces_only_target_section(self):
        out = replace_section(self.TEXT, "A", 2, "## A\nNEW")
        assert out == "# Title\n\nintro\n\n## A\nNEW\n## B\nB body\n"

    def test_missing_title_returns_none(self):
        assert replace_section(self.TEXT, "Nope", 2, "x") is None

    def test_duplicate_title_returns_none(self):
        text = "## A\none\n## B\ntwo\n## A\nthree\n"
        assert replace_section(text, "A", 2, "x") is None

    def test_preamble_replacement(self):
        out = replace_section(self.TEXT, "", 0, "NEW PREAMBLE")
        assert out.startswith("NEW PREAMBLE\n# Title")

    def test_last_section_to_eof(self):
        out = replace_section(self.TEXT, "B", 2, "## B\nnew B")
        assert out.endswith("## B\nnew B\n")

    def test_heading_lookalike_in_fence_not_a_boundary(self):
        text = "## A\nbody\n```\n## fake\n```\n## B\nB body\n"
        out = replace_section(text, "A", 2, "## A\nnew")
        assert out == "## A\nnew\n## B\nB body\n"

    def test_removal_with_empty_rewrite(self):
        out = replace_section(self.TEXT, "A", 2, "")
        assert "A body" not in out
        assert "## B" in out

    def test_trailing_newline_preserved(self):
        text = "# T\ncontent\n"
        out = replace_section(text, "T", 1, "# T\nnew")
        assert out.endswith("\n")
