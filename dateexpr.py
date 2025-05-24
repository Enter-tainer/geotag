import re
import datetime
from typing import Any, List, Tuple, Union, Optional, NamedTuple


# --- Custom Exceptions ---
class DateExpressionError(Exception):
    """Base class for errors in this module."""

    pass


class DateExpressionSyntaxError(DateExpressionError):
    """Raised for syntax errors in the expression."""

    pass


class InvalidDateLiteralError(DateExpressionError):
    """Raised for invalid date literal formats."""

    pass


# --- Tokenizer (Lexer) ---
class Token(NamedTuple):
    type: str
    value: str
    line: int  # For future use, not strictly needed by this parser
    column: int  # For future use


# Token specifications
# Order matters for regex matching
TOKEN_SPECIFICATION = [
    ("SKIP", r"[ \t]+"),  # Skip whitespace
    ("LPAREN", r"\("),  # (
    ("RPAREN", r"\)"),  # )
    ("OPERATOR", r">=|<=|==|!=|>|<|="),  # Comparison operators
    ("DATE_VAR", r"date\b"),  # The 'date' variable
    ("LOGICAL_OP_AND", r"(?i:and)\b"),  # AND (case-insensitive)
    ("LOGICAL_OP_OR", r"(?i:or)\b"),  # OR (case-insensitive)
    # Date literals are tricky. We'll capture potential date/timestamp strings.
    # ISO 8601-like with T, optional Z or offset
    (
        "ISO_DATETIME_STR",
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?",
    ),
    # YYYY-MM-DD or YYYY/MM/DD
    ("DATE_STR_SEP", r"\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"),
    # YYYYMMDD or Unix Timestamp (numbers)
    ("NUMBER_STR", r"\d+\b"),
    ("MISMATCH", r"."),  # Any other character
]

TOKEN_REGEX = re.compile("|".join("(?P<%s>%s)" % pair for pair in TOKEN_SPECIFICATION))


def tokenize(code: str) -> List[Token]:
    tokens = []
    line_num = 1
    line_start = 0
    for mo in TOKEN_REGEX.finditer(code):
        kind = mo.lastgroup
        value = mo.group()
        column = mo.start() - line_start
        if kind == "SKIP":
            pass
        elif kind == "MISMATCH":
            raise DateExpressionSyntaxError(
                f"Unexpected character: '{value}' at line {line_num} column {column}"
            )
        else:
            # Normalize logical operators to a single type for the parser
            if kind == "LOGICAL_OP_AND":
                kind = "LOGICAL_OP"
                value = "and"
            elif kind == "LOGICAL_OP_OR":
                kind = "LOGICAL_OP"
                value = "or"
            tokens.append(Token(kind, value, line_num, column))
    tokens.append(Token("EOF", "", line_num, len(code)))  # End of File token
    return tokens


# --- AST Nodes ---
class ASTNode:
    pass


class TypedLiteral(ASTNode):
    def __init__(self, type: str, value: Any):
        # type: 'timestamp', 'day_int', 'datetime_obj'
        self.type = type
        self.value = value

    def __repr__(self):
        return f"TypedLiteral({self.type!r}, {self.value!r})"


class ComparisonNode(ASTNode):
    def __init__(self, var_name: str, operator: str, literal: TypedLiteral):
        self.var_name = var_name  # Should always be 'date'
        self.operator = operator
        self.literal = literal

    def __repr__(self):
        return f"ComparisonNode({self.var_name!r} {self.operator} {self.literal!r})"


class LogicalOpNode(ASTNode):
    def __init__(self, left: ASTNode, operator: str, right: ASTNode):
        self.left = left
        self.operator = operator.lower()  # 'and' or 'or'
        self.right = right

    def __repr__(self):
        return f"LogicalOpNode({self.left!r} {self.operator} {self.right!r})"


# --- Parser ---
class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
        self.current_token: Optional[Token] = (
            self.tokens[self.pos] if self.tokens else None
        )

    def _advance(self):
        self.pos += 1
        if self.pos < len(self.tokens):
            self.current_token = self.tokens[self.pos]
        else:
            self.current_token = Token(
                "EOF", "", -1, -1
            )  # Should match EOF token from lexer

    def _eat(self, token_type: str):
        if self.current_token and self.current_token.type == token_type:
            self._advance()
        else:
            expected = token_type
            found = self.current_token.type if self.current_token else "None"
            val = self.current_token.value if self.current_token else ""
            raise DateExpressionSyntaxError(
                f"Expected token {expected} but found {found} ('{val}')"
            )

    def _parse_date_literal_value(self, token: Token) -> TypedLiteral:
        value_str = token.value

        # Try ISO 8601 datetime string
        if token.type == "ISO_DATETIME_STR":
            try:
                dt_obj = datetime.datetime.fromisoformat(value_str)
                return TypedLiteral("datetime_obj", dt_obj)
            except ValueError:
                raise InvalidDateLiteralError(
                    f"Invalid ISO 8601 datetime format: {value_str}"
                )

        # Try YYYY-MM-DD or YYYY/MM/DD
        if token.type == "DATE_STR_SEP":
            try:
                # Normalize separators for parsing
                normalized_date_str = value_str.replace("/", "-")
                dt_obj = datetime.datetime.strptime(normalized_date_str, "%Y-%m-%d")
                return TypedLiteral(
                    "day_int", dt_obj.year * 10000 + dt_obj.month * 100 + dt_obj.day
                )
            except ValueError:
                raise InvalidDateLiteralError(
                    f"Invalid date format (YYYY-MM-DD or YYYY/MM/DD): {value_str}"
                )

        # Try NUMBER_STR (could be YYYYMMDD or Unix timestamp)
        if token.type == "NUMBER_STR":
            try:
                num = int(value_str)
            except ValueError:  # Should not happen if lexer is correct
                raise InvalidDateLiteralError(
                    f"Invalid number for date/timestamp: {value_str}"
                )

            # Heuristic: 8 digits and plausible date? -> YYYYMMDD
            if len(value_str) == 8:
                try:
                    year, month, day = (
                        int(value_str[0:4]),
                        int(value_str[4:6]),
                        int(value_str[6:8]),
                    )
                    # Basic validation
                    datetime.datetime(
                        year, month, day
                    )  # Will raise ValueError if invalid date
                    return TypedLiteral("day_int", num)
                except ValueError:
                    # Not a valid YYYYMMDD, could be a timestamp or other number
                    pass  # Fall through to timestamp check

            # Assume it's a Unix timestamp (integer seconds)
            # A more robust check might involve typical range of timestamps
            if (
                len(value_str) >= 9 and len(value_str) <= 11
            ):  # Plausible length for seconds timestamp
                return TypedLiteral("timestamp", num)

            # If it didn't fit YYYYMMDD and doesn't look like a common timestamp, error.
            # Or, if it was 8 digits but invalid date, it's an error here.
            if len(value_str) == 8:  # Was 8 digits but not valid YYYYMMDD
                raise InvalidDateLiteralError(f"Invalid YYYYMMDD date: {value_str}")
            else:  # Other numbers not fitting timestamp heuristic
                raise InvalidDateLiteralError(
                    f"Ambiguous number or unsupported timestamp format: {value_str}. Use YYYYMMDD, Unix timestamp (seconds), or full ISO datetime."
                )

        raise InvalidDateLiteralError(
            f"Unknown or unhandled date literal format for token: {token}"
        )

    def _parse_factor(self) -> ASTNode:
        token = self.current_token
        if token.type == "DATE_VAR":
            self._eat("DATE_VAR")
            var_name = "date"  # from token.value, but we know it's 'date'

            op_token = self.current_token
            if op_token.type != "OPERATOR":
                raise DateExpressionSyntaxError(
                    f"Expected operator after 'date', got {op_token.type}"
                )
            self._eat("OPERATOR")

            date_literal_token = self.current_token
            # The token type from lexer helps guide parsing here
            if date_literal_token.type not in (
                "NUMBER_STR",
                "DATE_STR_SEP",
                "ISO_DATETIME_STR",
            ):
                raise DateExpressionSyntaxError(
                    f"Expected a date/time literal, got {date_literal_token.type} ('{date_literal_token.value}')"
                )

            typed_literal = self._parse_date_literal_value(date_literal_token)
            self._advance()  # Consume the date literal token itself

            return ComparisonNode(var_name, op_token.value, typed_literal)

        elif token.type == "LPAREN":
            self._eat("LPAREN")
            node = self._parse_expression()
            self._eat("RPAREN")
            return node
        else:
            raise DateExpressionSyntaxError(
                f"Invalid factor: Expected 'date' or '(', got {token.type} ('{token.value}')"
            )

    def _parse_term(self) -> ASTNode:  # Handles AND
        node = self._parse_factor()
        while (
            self.current_token
            and self.current_token.type == "LOGICAL_OP"
            and self.current_token.value.lower() == "and"
        ):
            op_token = self.current_token
            self._eat("LOGICAL_OP")
            right_node = self._parse_factor()
            node = LogicalOpNode(left=node, operator=op_token.value, right=right_node)
        return node

    def _parse_expression(self) -> ASTNode:  # Handles OR
        node = self._parse_term()
        while (
            self.current_token
            and self.current_token.type == "LOGICAL_OP"
            and self.current_token.value.lower() == "or"
        ):
            op_token = self.current_token
            self._eat("LOGICAL_OP")
            right_node = self._parse_term()
            node = LogicalOpNode(left=node, operator=op_token.value, right=right_node)
        return node

    def parse(self) -> ASTNode:
        ast = self._parse_expression()
        if self.current_token and self.current_token.type != "EOF":
            raise DateExpressionSyntaxError(
                f"Unexpected token {self.current_token.value} at end of expression"
            )
        return ast


# --- Main Parser Class ---
class DateExpressionParser:
    def __init__(self, expression_string: str):
        self.expression_string = expression_string
        try:
            tokens = tokenize(self.expression_string)
            self.ast = Parser(tokens).parse()
        except DateExpressionError as e:
            # Re-raise with more context or allow original exception
            raise e  # Or wrap it: raise DateExpressionError(f"Failed to parse '{expression_string}': {e}") from e

    def _evaluate_node(
        self, node: ASTNode, file_datetime_obj: datetime.datetime
    ) -> bool:
        if isinstance(node, ComparisonNode):
            literal_type = node.literal.type
            literal_val = node.literal.value
            op_str = node.operator

            file_val_comp: Union[int, datetime.datetime]
            lit_val_comp: Union[int, datetime.datetime]

            if literal_type == "timestamp":
                # file_datetime_obj is local. timestamp() gives UTC epoch seconds.
                # literal_val is a timestamp, assumed to be UTC epoch seconds.
                file_val_comp = int(file_datetime_obj.timestamp())
                lit_val_comp = literal_val
            elif literal_type == "day_int":  # YYYYMMDD
                file_val_comp = (
                    file_datetime_obj.year * 10000
                    + file_datetime_obj.month * 100
                    + file_datetime_obj.day
                )
                lit_val_comp = literal_val
            elif literal_type == "datetime_obj":  # literal_val is a datetime object
                f_dt = file_datetime_obj
                l_dt = literal_val  # This is a datetime object

                if (f_dt.tzinfo is None) and (l_dt.tzinfo is not None):
                    # File is naive (local), literal is aware. Convert file to aware local.
                    try:
                        f_dt = f_dt.astimezone()
                    except (
                        ValueError
                    ) as e:  # Handle cases like pre-epoch naive dates on some systems
                        raise DateExpressionError(
                            f"Cannot make naive datetime {f_dt} timezone-aware for comparison: {e}"
                        )

                elif (f_dt.tzinfo is not None) and (l_dt.tzinfo is None):
                    # File is aware, literal is naive (local). Convert file to naive local.
                    f_dt = f_dt.astimezone(None)

                file_val_comp = f_dt
                lit_val_comp = l_dt
            else:
                raise DateExpressionError(
                    f"Unknown literal type during evaluation: {literal_type}"
                )

            if op_str == ">":
                return file_val_comp > lit_val_comp
            elif op_str == ">=":
                return file_val_comp >= lit_val_comp
            elif op_str == "<":
                return file_val_comp < lit_val_comp
            elif op_str == "<=":
                return file_val_comp <= lit_val_comp
            elif op_str == "=" or op_str == "==":
                return file_val_comp == lit_val_comp
            elif op_str == "!=":
                return file_val_comp != lit_val_comp
            else:
                raise DateExpressionError(
                    f"Unknown operator during evaluation: {op_str}"
                )

        elif isinstance(node, LogicalOpNode):
            left_result = self._evaluate_node(node.left, file_datetime_obj)
            if node.operator == "and":
                if not left_result:
                    return False  # Short-circuit
                return self._evaluate_node(node.right, file_datetime_obj)
            elif node.operator == "or":
                if left_result:
                    return True  # Short-circuit
                return self._evaluate_node(node.right, file_datetime_obj)
            else:
                raise DateExpressionError(f"Unknown logical operator: {node.operator}")

        raise DateExpressionError("Unknown AST node type during evaluation")

    def evaluate(self, file_datetime_obj: datetime.datetime) -> bool:
        """
        Evaluates the parsed date expression against the given file datetime.
        :param file_datetime_obj: A datetime.datetime object representing the file's date/time.
                                  If naive, it's assumed to be in the local timezone.
        :return: True if the expression matches, False otherwise.
        """
        if not isinstance(file_datetime_obj, datetime.datetime):
            raise TypeError("file_datetime_obj must be a datetime.datetime object")
        return self._evaluate_node(self.ast, file_datetime_obj)


if __name__ == "__main__":
    # --- Test Cases from Spec ---
    print("Running test cases...")

    # Example 1: date > 20250101
    try:
        parser1 = DateExpressionParser("date > 20250101")
        # Assuming current date is 2025-05-25 for these relative tests
        assert parser1.evaluate(datetime.datetime(2025, 5, 25)) == True, (
            "Test 1.1 failed"
        )
        assert parser1.evaluate(datetime.datetime(2024, 12, 31)) == False, (
            "Test 1.2 failed"
        )
        print("Test 1 (date > 20250101) passed.")
    except Exception as e:
        print(f"Test 1 failed with error: {e}")

    # Example 2: date >= 2024-01-01 and date < 2025-01-01
    try:
        parser2 = DateExpressionParser("date >= 2024-01-01 and date < 2025-01-01")
        assert parser2.evaluate(datetime.datetime(2024, 6, 1)) == True, (
            "Test 2.1 failed (spec example)"
        )
        assert parser2.evaluate(datetime.datetime(2025, 6, 1)) == False, (
            "Test 2.2 failed (spec example)"
        )
        assert parser2.evaluate(datetime.datetime(2024, 1, 1)) == True, (
            "Test 2.3 failed (lower bound)"
        )
        assert parser2.evaluate(datetime.datetime(2023, 12, 31)) == False, (
            "Test 2.4 failed (below lower bound)"
        )
        assert parser2.evaluate(datetime.datetime(2025, 1, 1)) == False, (
            "Test 2.5 failed (upper bound exclusive)"
        )
        assert parser2.evaluate(datetime.datetime(2024, 12, 31, 23, 59, 59)) == True, (
            "Test 2.6 failed (just below upper bound)"
        )
        print("Test 2 (date >= 2024-01-01 and date < 2025-01-01) passed.")
    except Exception as e:
        print(f"Test 2 failed with error: {e}")

    # Example 3: date != 2025/05/25 or date = 2026-01-01
    try:
        parser3 = DateExpressionParser("date != 2025/05/25 or date = 2026-01-01")
        assert parser3.evaluate(datetime.datetime(2025, 5, 25)) == False, (
            "Test 3.1 failed (date is 2025/05/25)"
        )  # != is false, = is false -> false
        # Correction: if date is 2025/05/25, (date != 2025/05/25) is False. (date = 2026-01-01) is False. False or False = False.
        # If date is 2026/01/01, (date != 2025/05/25) is True. (date = 2026-01-01) is True. True or True = True.
        assert parser3.evaluate(datetime.datetime(2026, 1, 1)), (
            "Test 3.2 failed (date is 2026/01/01)"
        )
        assert parser3.evaluate(datetime.datetime(2025, 1, 1)), (
            "Test 3.3 failed (date is not 2025/05/25)"
        )
        print("Test 3 (date != 2025/05/25 or date = 2026-01-01) passed.")
    except Exception as e:
        print(f"Test 3 failed with error: {e}")

    # Example 4: (date > 20240101 and date < 20250101) or date = 20251212
    try:
        parser4 = DateExpressionParser(
            "(date > 20240101 and date < 20250101) or date = 20251212"
        )
        assert parser4.evaluate(datetime.datetime(2024, 6, 15)), (
            "Test 4.1 failed (in range)"
        )
        assert parser4.evaluate(datetime.datetime(2025, 12, 12)), (
            "Test 4.2 failed (equals specific date)"
        )
        assert not parser4.evaluate(datetime.datetime(2023, 1, 1)), (
            "Test 4.3 failed (outside both)"
        )
        assert not parser4.evaluate(datetime.datetime(2025, 1, 1)), (
            "Test 4.4 failed (on boundary, not in range, not specific date)"
        )
        assert parser4.evaluate(datetime.datetime(2024, 12, 31, 23, 59, 59)), (
            "Test 4.5 failed (just before upper bound)"
        )
        print(
            "Test 4 ((date > 20240101 and date < 20250101) or date = 20251212) passed."
        )
    except Exception as e:
        print(f"Test 4 failed with error: {e}")

    # Test Unix Timestamp
    # 1716633600 is 2024-05-25 10:40:00 UTC if my converter is right.
    # Let's use a known timestamp: datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc).timestamp() -> 1704067200
    ts_jan1_2024_utc = 1704067200
    # dt_jan1_2024_local = datetime.datetime(2024, 1, 1, 0, 0, 0) # Naive local - removed as unused
    # The .timestamp() method on a naive datetime assumes it's local time and converts to UTC timestamp.
    # So, if dt_jan1_2024_local.timestamp() is used, it will match ts_jan1_2024_utc if local IS UTC.
    # This part is tricky due to local timezones.
    # Let's test with a local datetime that would correspond to a specific UTC timestamp.
    # For simplicity, let's use a datetime object that we know the timestamp of.
    # dt_test_ts = datetime.datetime(2024, 5, 25, 12, 0, 0) # Local time
    # ts_test_val = int(dt_test_ts.timestamp()) # Timestamp for this local time

    # Test with a fixed timestamp: 1704067200 (2024-01-01 00:00:00 UTC)
    # We need a local datetime that, when converted to timestamp, matches this.
    # This depends on the local timezone of the machine running the test.
    # A more robust test uses datetime objects with explicit timezones for timestamp literals.
    # However, the spec says "Unix timestamp ... directly with file timestamp comparison".
    # And file_datetime_obj.timestamp() is used.
    try:
        # Test date > timestamp
        # Let's use a timestamp for 2024-01-01 00:00:00 UTC
        # Python: int(datetime.datetime(2024,1,1, tzinfo=datetime.timezone.utc).timestamp()) == 1704067200
        literal_ts_val_for_gt = ts_jan1_2024_utc - 1  # date > 2023-12-31 23:59:59 UTC
        parser_ts = DateExpressionParser(f"date > {literal_ts_val_for_gt}")

        # Create a naive local datetime that is definitely after the literal_ts_val_for_gt (in UTC)
        # dt_after_ts's timestamp will be ts_jan1_2024_utc
        dt_after_ts = datetime.datetime.fromtimestamp(ts_jan1_2024_utc)
        assert parser_ts.evaluate(dt_after_ts), (
            f"Test TS.1 failed: {dt_after_ts.timestamp()} not > {literal_ts_val_for_gt}"
        )

        # Create a naive local datetime for equality test
        dt_for_eq_test = datetime.datetime.fromtimestamp(
            ts_jan1_2024_utc + 3600
        )  # 1 hour after
        ts_for_eq_test = int(dt_for_eq_test.timestamp())
        parser_ts_eq = DateExpressionParser(f"date = {ts_for_eq_test}")
        assert parser_ts_eq.evaluate(dt_for_eq_test), (
            f"Test TS.2 failed (equality): {dt_for_eq_test.timestamp()} != {ts_for_eq_test}"
        )
        print("Test Unix Timestamp passed (basic).")
    except Exception as e:
        print(f"Test Unix Timestamp failed with error: {e}")

    # Test ISO 8601 Datetime
    try:
        # ISO without offset (local time)
        parser_iso1 = DateExpressionParser("date = 2025-05-25T12:30:00")
        assert parser_iso1.evaluate(datetime.datetime(2025, 5, 25, 12, 30, 0)), (
            "Test ISO1.1 failed"
        )
        assert not parser_iso1.evaluate(datetime.datetime(2025, 5, 25, 12, 30, 1)), (
            "Test ISO1.2 failed"
        )

        # ISO with Z (UTC)
        parser_iso_z = DateExpressionParser("date = 2025-05-25T12:30:00Z")
        # Input datetime is naive (local). It will be made aware for comparison.
        # This test's success depends on the local timezone.
        # If local is UTC, datetime(2025,5,25,12,30,0) will match.
        # If local is UTC+2, then datetime(2025,5,25,14,30,0) local would be 12:30:00Z.
        # Let's test with an aware datetime input
        dt_utc = datetime.datetime(2025, 5, 25, 12, 30, 0, tzinfo=datetime.timezone.utc)
        assert parser_iso_z.evaluate(dt_utc), "Test ISO_Z.1 failed (aware input)"

        # Test with naive input that should match UTC if local time is set up correctly
        # This is the tricky case: comparing naive local with aware UTC literal

        # Test ISO_Z.2: Test interaction of naive local datetime inputs with an aware UTC literal.
        # parser_iso_z is for "date = 2025-05-25T12:30:00Z"
        utc_literal_in_expr = datetime.datetime(
            2025, 5, 25, 12, 30, 0, tzinfo=datetime.timezone.utc
        )

        # Input A: A naive datetime whose wall-clock time is the same as the UTC literal's wall-clock time.
        # e.g., if literal is 12:30:00Z, this input is naive 12:30:00.
        # This will match the literal IF AND ONLY IF the local system timezone is UTC.
        dt_naive_same_clock_as_utc = datetime.datetime(
            utc_literal_in_expr.year,
            utc_literal_in_expr.month,
            utc_literal_in_expr.day,
            utc_literal_in_expr.hour,
            utc_literal_in_expr.minute,
            utc_literal_in_expr.second,
        )
        eval_A_matches = parser_iso_z.evaluate(dt_naive_same_clock_as_utc)

        # Input B: A naive datetime that represents the local time equivalent of the UTC literal.
        # e.g., if literal is 12:30:00Z and local is UTC+2, this input is naive 14:30:00.
        # This should ALWAYS match the literal, regardless of local timezone.
        dt_naive_local_equivalent_of_utc = utc_literal_in_expr.astimezone(None)
        eval_B_matches = parser_iso_z.evaluate(dt_naive_local_equivalent_of_utc)

        assert eval_B_matches, (
            "Test ISO_Z.2a failed: Naive local equivalent of UTC literal should always match."
        )

        # Check local timezone offset
        local_tz_offset_seconds = 0  # Default to UTC
        now_dt = datetime.datetime.now()
        aware_now_dt = (
            now_dt.astimezone()
        )  # Make it aware using system's local timezone
        current_offset = aware_now_dt.utcoffset()
        if current_offset is not None:
            local_tz_offset_seconds = current_offset.total_seconds()

        if local_tz_offset_seconds != 0:  # If local system timezone is NOT UTC
            assert not eval_A_matches, (
                "Test ISO_Z.2b failed: Naive with same clock as UTC literal should NOT match if local system TZ is not UTC."
            )
        else:  # If local system timezone IS UTC
            assert eval_A_matches, (
                "Test ISO_Z.2c failed: Naive with same clock as UTC literal SHOULD match if local system TZ is UTC."
            )

        # ISO with offset
        # Assuming current machine is not UTC-05:00 for this to be a distinct test
        parser_iso_offset = DateExpressionParser(
            "date = 2025-05-25T10:30:00-05:00"
        )  # This is 2025-05-25 15:30:00 UTC
        dt_match_offset_utc = datetime.datetime(
            2025, 5, 25, 15, 30, 0, tzinfo=datetime.timezone.utc
        )
        assert parser_iso_offset.evaluate(dt_match_offset_utc), (
            "Test ISO_Offset.1 failed"
        )

        print("Test ISO 8601 Datetime passed (basic).")
    except Exception as e:
        print(f"Test ISO 8601 Datetime failed with error: {e}")

    # --- 补充复杂成功用例 ---
    print("\nRunning additional complex test cases...")
    try:
        # 1. 多层括号嵌套与混合 and/or
        parser = DateExpressionParser(
            "((date > 20240101 and date < 20241231) or (date = 20250101)) and date != 20240303"
        )
        assert parser.evaluate(
            datetime.datetime(2024, 6, 1)
        )  # True (in range, not 20240303)
        assert parser.evaluate(datetime.datetime(2025, 1, 1))  # True (date = 20250101)
        assert not parser.evaluate(
            datetime.datetime(2024, 3, 3)
        )  # False (排除20240303)
        assert not parser.evaluate(datetime.datetime(2023, 12, 31))  # False (不在区间)
        print("Test C1 (nested and/or, exclusion) passed.")

        # 2. 不同日期格式混用
        parser = DateExpressionParser("date >= 2024-06-01 and date < 20240605")
        assert parser.evaluate(datetime.datetime(2024, 6, 1))
        assert parser.evaluate(datetime.datetime(2024, 6, 4, 23, 59, 59))
        assert not parser.evaluate(datetime.datetime(2024, 6, 5))
        print("Test C2 (mixed date format) passed.")

        # 3. 精确到秒的 ISO 8601 比较
        parser = DateExpressionParser("date = 2025-05-25T12:34:56")
        assert parser.evaluate(datetime.datetime(2025, 5, 25, 12, 34, 56))
        assert not parser.evaluate(datetime.datetime(2025, 5, 25, 12, 34, 55))
        print("Test C3 (ISO 8601 second precision) passed.")

        # 4. 时区偏移的等价性
        parser = DateExpressionParser("date = 2025-05-25T10:00:00+02:00")
        # 2025-05-25T08:00:00Z == 2025-05-25T10:00:00+02:00
        dt_utc = datetime.datetime(2025, 5, 25, 8, 0, 0, tzinfo=datetime.timezone.utc)
        assert parser.evaluate(dt_utc)
        print("Test C4 (timezone offset equivalence) passed.")

        # 5. 边界值
        parser = DateExpressionParser("date >= 20240601 and date <= 20240630")
        assert parser.evaluate(datetime.datetime(2024, 6, 1))
        assert parser.evaluate(datetime.datetime(2024, 6, 30))
        assert not parser.evaluate(datetime.datetime(2024, 5, 31))
        assert not parser.evaluate(datetime.datetime(2024, 7, 1))
        print("Test C5 (boundary values) passed.")

        # 6. 复杂短路逻辑
        parser = DateExpressionParser(
            "date = 20250101 or (date > 20250101 and date < 20250110)"
        )
        assert parser.evaluate(
            datetime.datetime(2025, 1, 1)
        )  # True (左侧满足，右侧不计算)
        assert parser.evaluate(datetime.datetime(2025, 1, 5))  # True (右侧满足)
        assert not parser.evaluate(datetime.datetime(2025, 1, 10))  # False (右侧不满足)
        print("Test C6 (complex short-circuit logic) passed.")

        # 7. Unix 时间戳与日期混合
        ts_20240601 = int(datetime.datetime(2024, 6, 1, 0, 0, 0).timestamp())
        parser = DateExpressionParser(f"date >= {ts_20240601} and date < 2024-06-02")
        assert parser.evaluate(datetime.datetime(2024, 6, 1, 12, 0, 0))
        assert not parser.evaluate(datetime.datetime(2024, 6, 2, 0, 0, 0))
        print("Test C7 (timestamp and date mix) passed.")

        # 8. 复杂括号优先级
        parser = DateExpressionParser(
            "date = 20240601 or date = 20240602 and date = 20240603"
        )
        # 按优先级应为: date = 20240601 or (date = 20240602 and date = 20240603)
        assert parser.evaluate(datetime.datetime(2024, 6, 1))  # True
        assert not parser.evaluate(datetime.datetime(2024, 6, 2))  # False
        assert not parser.evaluate(datetime.datetime(2024, 6, 3))  # False
        print("Test C8 (parenthesis precedence) passed.")

        print("All additional complex test cases passed.")
    except Exception as e:
        print(f"Complex test cases failed with error: {e}")

    # Error handling tests
    error_expressions = [
        "date > 20250101 and",  # Incomplete
        "date & 20250101",  # Invalid operator
        "date > 2025-13-01",  # Invalid date
        "date = 123",  # Ambiguous number (not YYYYMMDD, not typical timestamp length)
        "(date > 20250101",  # Mismatched parens
        "mydate > 20250101",  # Invalid variable
    ]
    print("\nTesting error handling:")
    for i, expr_str in enumerate(error_expressions):
        try:
            DateExpressionParser(expr_str)
            print(
                f"Error Test {i + 1} ('{expr_str}') FAILED: Expected an error, but none was raised."
            )
        except DateExpressionError as e:
            print(
                f"Error Test {i + 1} ('{expr_str}') PASSED: Raised {type(e).__name__}: {e}"
            )
        except Exception as e:
            print(
                f"Error Test {i + 1} ('{expr_str}') FAILED: Raised unexpected error {type(e).__name__}: {e}"
            )

    print("\nAll tests finished.")
