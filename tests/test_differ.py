from src.differ import FileDiff, parse_diff

ADDED_DIFF = """\
diff --git a/newmod.py b/newmod.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/newmod.py
@@ -0,0 +1,3 @@
+def hello():
+    return "hi"
"""

DELETED_DIFF = """\
diff --git a/old.py b/old.py
deleted file mode 100644
index 1234567..0000000
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def bye():
-    return "bye"
"""

MODIFIED_TWO_HUNKS = """\
diff --git a/calc.py b/calc.py
index 1111111..2222222 100644
--- a/calc.py
+++ b/calc.py
@@ -1,3 +1,3 @@
-def add(a, b):
-    return a + b
+def add(a, b=0):
+    return a + b
@@ -10,2 +10,3 @@
 def sub(a, b):
     return a - b
+def mul(a, b):
+    return a * b
"""

RENAMED_DIFF = """\
diff --git a/util.py b/helpers.py
similarity index 95%
rename from util.py
rename to helpers.py
index 1111111..2222222 100644
--- a/util.py
+++ b/helpers.py
@@ -1,1 +1,1 @@
-HELP = "hi"
+HELP = "hello"
"""


class TestParseDiff:
    def test_added_file(self):
        files = parse_diff(ADDED_DIFF)
        assert len(files) == 1
        f = files[0]
        assert f.path == "newmod.py"
        assert f.status == "added"
        assert len(f.hunks) == 1
        assert "@@ -0,0 +1,3 @@" in f.hunks[0]
        assert '+def hello():\n+    return "hi"' in f.hunks[0]

    def test_deleted_file(self):
        files = parse_diff(DELETED_DIFF)
        assert len(files) == 1
        f = files[0]
        assert f.path == "old.py"
        assert f.status == "deleted"
        assert len(f.hunks) == 1

    def test_modified_file_with_multiple_hunks(self):
        files = parse_diff(MODIFIED_TWO_HUNKS)
        assert len(files) == 1
        f = files[0]
        assert f.path == "calc.py"
        assert f.status == "modified"
        assert len(f.hunks) == 2
        assert "def mul(a, b):" in f.hunks[1]

    def test_renamed_file(self):
        files = parse_diff(RENAMED_DIFF)
        assert len(files) == 1
        f = files[0]
        assert f.path == "helpers.py"
        assert f.status == "renamed"
        assert len(f.hunks) == 1

    def test_multiple_files(self):
        text = ADDED_DIFF + "\n" + DELETED_DIFF
        files = parse_diff(text)
        assert [f.path for f in files] == ["newmod.py", "old.py"]
        assert [f.status for f in files] == ["added", "deleted"]

    def test_empty_diff(self):
        assert parse_diff("") == []

    def test_no_hunks_file(self):
        text = (
            "diff --git a/img.png b/img.png\n"
            "index 111..222 100644\n"
            "Binary files a/img.png and b/img.png differ\n"
        )
        files = parse_diff(text)
        assert files == [FileDiff(path="img.png", status="modified", hunks=[])]

    def test_quoted_path_with_space(self):
        text = (
            'diff --git "a/my file.py" "b/my file.py"\n'
            "index 111..222 100644\n"
            '--- "a/my file.py"\n'
            '+++ "b/my file.py"\n'
            "@@ -1,2 +1,2 @@\n"
            " def f():\n"
            "-    return 1\n"
            "+    return 2\n"
        )
        files = parse_diff(text)
        assert files[0].path == "my file.py"
        assert files[0].status == "modified"
        assert len(files[0].hunks) == 1

    def test_quoted_path_with_octal_escape(self):
        text = (
            'diff --git "a/caf\\303\\251.md" "b/caf\\303\\251.md"\n'
            "index 111..222 100644\n"
            '--- "a/caf\\303\\251.md"\n'
            '+++ "b/caf\\303\\251.md"\n'
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        files = parse_diff(text)
        assert files[0].path == "café.md"

    def test_at_sign_lookalike_content_line_stays_in_hunk(self):
        text = (
            "diff --git a/notes.md b/notes.md\n"
            "index 111..222 100644\n"
            "--- a/notes.md\n"
            "+++ b/notes.md\n"
            "@@ -1,3 +1,4 @@\n"
            " # Example\n"
            " @@ -1,1 +1,1 @@\n"
            "+more text\n"
        )
        files = parse_diff(text)
        assert len(files[0].hunks) == 1
        assert "@@ -1,1 +1,1 @@" in files[0].hunks[0]

    def test_mode_only_change(self):
        text = (
            "diff --git a/run.sh b/run.sh\n"
            "old mode 100644\n"
            "new mode 100755\n"
        )
        files = parse_diff(text)
        assert files == [FileDiff(path="run.sh", status="modified", hunks=[])]

    def test_no_newline_marker_stays_in_hunk(self):
        text = (
            "diff --git a/x.py b/x.py\n"
            "index 111..222 100644\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "\\ No newline at end of file\n"
            "+b\n"
            "\\ No newline at end of file\n"
        )
        files = parse_diff(text)
        assert len(files[0].hunks) == 1
        assert "\\ No newline at end of file" in files[0].hunks[0]

    def test_rename_with_edits_and_quoted_path(self):
        text = (
            'diff --git "a/old name.py" "b/new name.py"\n'
            "similarity index 90%\n"
            'rename from "old name.py"\n'
            'rename to "new name.py"\n'
            "index 111..222 100644\n"
            '--- "a/old name.py"\n'
            '+++ "b/new name.py"\n'
            "@@ -1,1 +1,1 @@\n"
            "-X = 1\n"
            "+X = 2\n"
        )
        files = parse_diff(text)
        assert files[0].path == "new name.py"
        assert files[0].status == "renamed"
        assert len(files[0].hunks) == 1

    def test_submodule_change(self):
        text = (
            "diff --git a/vendor b/vendor\n"
            "index 111..222 160000\n"
            "--- a/vendor\n"
            "+++ b/vendor\n"
        )
        files = parse_diff(text)
        assert files == [FileDiff(path="vendor", status="modified", hunks=[])]

    def test_gibberish_never_crashes(self):
        text = "this is not a diff\njust some text\n@ not a hunk\n"
        assert parse_diff(text) == []

    def test_crlf_line_endings(self):
        text = (
            "diff --git a/x.py b/x.py\r\n"
            "index 111..222 100644\r\n"
            "--- a/x.py\r\n"
            "+++ b/x.py\r\n"
            "@@ -1 +1 @@\r\n"
            "-a\r\n"
            "+b\r\n"
        )
        files = parse_diff(text)
        assert files[0].path == "x.py"
        assert len(files[0].hunks) == 1
