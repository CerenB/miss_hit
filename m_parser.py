#!/usr/bin/env python3
##############################################################################
##                                                                          ##
##          MATLAB Independent, Small & Safe, High Integrity Tools          ##
##                                                                          ##
##              Copyright (C) 2019, Florian Schanda                         ##
##              Copyright (C) 2019, Zenuity AB                              ##
##                                                                          ##
##  This file is part of MISS_HIT.                                          ##
##                                                                          ##
##  MATLAB Independent, Small & Safe, High Integrity Tools (MISS_HIT) is    ##
##  free software: you can redistribute it and/or modify it under the       ##
##  terms of the GNU General Public License as published by the Free        ##
##  Software Foundation, either version 3 of the License, or (at your       ##
##  option) any later version.                                              ##
##                                                                          ##
##  MISS_HIT is distributed in the hope that it will be useful,             ##
##  but WITHOUT ANY WARRANTY; without even the implied warranty of          ##
##  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           ##
##  GNU General Public License for more details.                            ##
##                                                                          ##
##  You should have received a copy of the GNU General Public License       ##
##  along with MISS_HIT. If not, see <http://www.gnu.org/licenses/>.        ##
##                                                                          ##
##############################################################################


from m_lexer import Token_Generator, MATLAB_Lexer
from errors import mh, ICE, Error, Location
import tree_print

# pylint: disable=wildcard-import,unused-wildcard-import
from m_ast import *
# pylint: enable=wildcard-import,unused-wildcard-import


IGNORED_TOKENS = frozenset(["COMMENT"])


class NIY(ICE):
    def __init__(self):
        super().__init__("not implemented yet")


# Operator precedence as of MATLAB 2019b
# https://www.mathworks.com/help/matlab/matlab_prog/operator-precedence.html
#
# 1. Parentheses ()
#
# 2. Transpose (.'), power (.^), complex conjugate transpose ('),
#    matrix power (^)
#
# 3. Power with unary minus (.^-), unary plus (.^+), or logical
#    negation (.^~) as well as matrix power with unary minus (^-), unary
#    plus (^+), or logical negation (^~).
#
#    Note: Although most operators work from left to right, the
#    operators (^-), (.^-), (^+), (.^+), (^~), and (.^~) work from
#    second from the right to left. It is recommended that you use
#    parentheses to explicitly specify the intended precedence of
#    statements containing these operator combinations.
#
# 4. Unary plus (+), unary minus (-), logical negation (~)
#
# 5. Multiplication (.*), right division (./), left division (.\),
#    matrix multiplication (*), matrix right division (/), matrix left
#    division (\)
#
# 6. Addition (+), subtraction (-)
#
# 7. Colon operator (:)
#
# 8. Less than (<), less than or equal to (<=), greater than (>),
#    greater than or equal to (>=), equal to (==), not equal to (~=)
#
# 9. Element-wise AND (&)
#
# 10. Element-wise OR (|)
#
# 11. Short-circuit AND (&&)
#
# 12. Short-circuit OR (||)

class MATLAB_Parser:
    def __init__(self, lexer):
        assert isinstance(lexer, Token_Generator)
        self.lexer = lexer

        # pylint: disable=invalid-name
        self.ct = None
        self.nt = None
        # pylint: enable=invalid-name

        self.next()

    def next(self):
        self.ct = self.nt
        self.nt = self.lexer.token()

        while self.nt:
            # Skip comments and continuations
            while self.nt and self.nt.kind in ("COMMENT", "CONTINUATION"):
                self.nt = self.lexer.token()

            # Join new-lines
            if (self.nt and
                self.ct and
                self.nt.kind == "NEWLINE" and
                self.ct.kind == "NEWLINE"):
                self.nt = self.lexer.token()
            else:
                break

    def match(self, kind, value=None):
        self.next()
        if self.ct is None:
            mh.error(Location(self.lexer.filename),
                     "expected %s, reached EOF instead" % kind)
        elif self.ct.kind != kind:
            mh.error(self.ct.location,
                     "expected %s, found %s instead" % (kind, self.ct.kind))
        elif value and self.ct.value() != value:
            mh.error(self.ct.location,
                     "expected %s(%s), found %s(%s) instead" %
                     (kind, value, self.ct.kind, self.ct.value()))

    def match_eof(self):
        self.next()
        if self.ct is not None:
            mh.error(self.ct.location,
                     "expected end of file, found %s instead" % self.ct.kind)

    def peek(self, kind, value=None):
        if self.nt and self.nt.kind == kind:
            if value is None:
                return True
            else:
                return self.nt.value() == value
        else:
            return False

    ##########################################################################
    # Parsing

    def parse_identifier(self, in_reference=False):
        if self.peek("OPERATOR", "~") and in_reference:
            self.match("OPERATOR")
            return Identifier(self.ct)
        else:
            self.match("IDENTIFIER")
            return Identifier(self.ct)

    def parse_selection(self, in_reference=False):
        rv = self.parse_identifier(in_reference)

        if rv.t_ident.value() == "~":
            return rv

        while self.peek("SELECTION"):
            self.match("SELECTION")
            dot = self.ct
            field = self.parse_identifier()
            rv = Selection(dot, rv, field)

        return rv

    def parse_file_input(self):
        while self.peek("NEWLINE") or self.peek("COMMENT"):
            self.next()

        if self.peek("KEYWORD", "function"):
            return self.parse_function_file()
        else:
            return self.parse_script_file()

    def parse_script_file(self):
        return self.parse_delimited_input()

    def parse_function_file(self):
        functions = []

        while self.peek("KEYWORD", "function"):
            functions.append(self.parse_function_def())
            if self.peek("NEWLINE"):
                self.match("NEWLINE")

        self.match_eof()

        for f in functions:
            tree_print.treepr(f)

        return functions

    def parse_function_def(self):
        self.match("KEYWORD", "function")
        t_fun = self.ct

        # Parse returns. Either 'x' or a list '[x, y]'
        returns = []
        if self.peek("A_BRA"):
            out_brackets = True
            self.match("A_BRA")
            if self.peek("A_KET"):
                self.match("A_KET")
            else:
                while True:
                    returns.append(self.parse_selection(in_reference=True))
                    if self.peek("COMMA"):
                        self.match("COMMA")
                    else:
                        break
                self.match("A_KET")
        else:
            out_brackets = False
            returns.append(self.parse_selection())

        if self.peek("BRA") and len(returns) == 1 and not out_brackets:
            # This is a function that doesn't return anything, so
            # function foo(...
            function_name = returns[0]
            returns = []

        elif self.peek("NEWLINE") and len(returns) == 1 and not out_brackets:
            # As above, but without the brackets
            function_name = returns[0]
            returns = []

        else:
            # This is a normal function, so something like
            # function [a, b] = potato...
            # function a = potato...
            self.match("ASSIGNMENT")
            function_name = self.parse_selection()

        inputs = []
        if self.peek("BRA"):
            self.match("BRA")
            if self.peek("KET"):
                self.match("KET")
            else:
                while True:
                    inputs.append(self.parse_identifier(in_reference=True))
                    if self.peek("COMMA"):
                        self.match("COMMA")
                    else:
                        break
                self.match("KET")

        if self.peek("SEMICOLON"):
            self.match("SEMICOLON")

        self.match("NEWLINE")

        body = self.parse_statement_list()

        return Function_Definition(t_fun, function_name, inputs, returns, body)

        # TODO: Build function entity

    def parse_statement_list(self):
        statements = []

        while not self.peek("KEYWORD", "end"):
            statements.append(self.parse_statement())

        self.match("KEYWORD", "end")

        return Sequence_Of_Statements(statements)

    def parse_delimited_input(self):
        statements = []

        while True:
            if self.peek("KEYWORD") and self.nt.value() in ("end",
                                                            "else",
                                                            "elseif"):
                break
            statements.append(self.parse_statement())

        return Sequence_Of_Statements(statements)

    def parse_statement(self):
        if self.peek("KEYWORD"):
            if self.nt.value() == "for":
                return self.parse_for_statement()
            elif self.nt.value() == "if":
                return self.parse_if_statement()
            elif self.nt.value() == "global":
                raise NIY()
            elif self.nt.value() == "while":
                return self.parse_while_statement()
            elif self.nt.value() == "return":
                return self.parse_return_statement()
            else:
                mh.error(self.nt.location,
                         "expected for|if|global|while|return,"
                         " found %s instead" % self.nt.value())

        else:
            return self.parse_assignment_or_call()

    def parse_assignment_or_call(self):
        # Assignment
        #   reference "=" expr                   '<ref> = <expr>'
        #   s_assignee_matrix "=" expr           '[<ref>] = <expr>'
        #   m_assignee_matrix "=" reference      '[<ref>, <ref>] = <ref>'
        # Call
        #   potato();                            '<ref>'
        #
        # TODO: need to make sure the call case has brackets.

        lhs = []
        if self.peek("A_BRA"):
            self.match("A_BRA")
            while True:
                lhs.append(self.parse_reference())
                if self.peek("COMMA"):
                    self.match("COMMA")
                else:
                    break
            self.match("A_KET")
        else:
            lhs.append(self.parse_reference())

        # This is the call case
        if len(lhs) == 1 and not self.peek("ASSIGNMENT"):
            self.match("SEMICOLON")
            self.match("NEWLINE")
            return Naked_Expression_Statement(lhs[0])

        self.match("ASSIGNMENT")
        t_eq = self.ct

        assert len(lhs) >= 1
        if len(lhs) == 1:
            rhs = self.parse_expression()
        else:
            rhs = self.parse_reference()

        self.match("SEMICOLON")
        self.match("NEWLINE")

        if len(lhs) == 1:
            return Simple_Assignment_Statement(t_eq, lhs[0], rhs)
        else:
            return Compound_Assignment_Statement(t_eq, lhs, rhs)

    def parse_reference(self):
        # identifier                   'potato'
        # identifier ( arglist )       'array(12)'
        # ident.field                  'foo.bar'
        # ident.field ( arglist )      'coord.x(12)'

        n_ident = self.parse_selection(in_reference=True)

        if self.peek("BRA"):
            arglist = self.parse_argument_list()
        else:
            arglist = []

        return Reference(n_ident, arglist)

    def parse_expression(self):
        return self.parse_precedence_12()

    # 1. Parentheses ()
    def parse_precedence_1(self):
        if self.peek("NUMBER"):
            self.match("NUMBER")
            return Number_Literal(self.ct)

        elif self.peek("STRING"):
            self.match("STRING")
            return String_Literal(self.ct)

        elif self.peek("BRA"):
            self.match("BRA")
            expr = self.parse_expression()
            self.match("KET")
            return expr

        elif self.peek("M_BRA"):
            return self.parse_matrix()

        else:
            return self.parse_reference()

    # 2. Transpose (.'), power (.^), complex conjugate transpose ('),
    #    matrix power (^)
    def parse_precedence_2(self):
        # TODO: fix chaining
        lhs = self.parse_precedence_1()

        if self.peek("OPERATOR") and self.nt.value() in ("'", ".'"):
            self.match("OPERATOR")
            return Unary_Operation(2, self.ct, lhs)

        elif self.peek("OPERATOR") and self.nt.value() in ("^", ".^"):
            self.match("OPERATOR")
            t_op = self.ct
            rhs = self.parse_precedence_1()
            return Binary_Operation(2, t_op, lhs, rhs)

        else:
            return lhs

    # 3. Power with unary minus (.^-), unary plus (.^+), or logical
    #    negation (.^~) as well as matrix power with unary minus (^-), unary
    #    plus (^+), or logical negation (^~).
    #
    #    Note: Although most operators work from left to right, the
    #    operators (^-), (.^-), (^+), (.^+), (^~), and (.^~) work from
    #    second from the right to left. It is recommended that you use
    #    parentheses to explicitly specify the intended precedence of
    #    statements containing these operator combinations.
    def parse_precedence_3(self):
        # TODO: actually implement this
        return self.parse_precedence_2()

    # 4. Unary plus (+), unary minus (-), logical negation (~)
    def parse_precedence_4(self):
        if self.peek("OPERATOR") and self.nt.value() in ("+", "-", "~"):
            self.match("OPERATOR")
            t_op = self.ct
            rhs = self.parse_precedence_4()
            return Unary_Operation(4, t_op, rhs)
        else:
            return self.parse_precedence_3()

    # 5. Multiplication (.*), right division (./), left division (.\),
    #    matrix multiplication (*), matrix right division (/), matrix left
    #    division (\)
    def parse_precedence_5(self):
        rv = self.parse_precedence_4()

        while self.peek("OPERATOR") and self.nt.value() in ("*", ".*",
                                                            "/", "./",
                                                            "\\", ".\\"):
            self.match("OPERATOR")
            t_op = self.ct
            rhs = self.parse_precedence_4()
            rv = Binary_Operation(5, t_op, rv, rhs)

        return rv

    # 6. Addition (+), subtraction (-)
    def parse_precedence_6(self):
        rv = self.parse_precedence_5()

        while self.peek("OPERATOR") and self.nt.value() in ("+", "-"):
            self.match("OPERATOR")
            t_op = self.ct
            rhs = self.parse_precedence_5()
            rv = Binary_Operation(6, t_op, rv, rhs)

        return rv

    # 7. Colon operator (:)
    def parse_range_expression(self):
        points = []
        points.append(self.parse_precedence_6())
        if self.peek("COLON"):
            self.match("COLON")
            points.append(self.parse_precedence_6())
        if self.peek("COLON"):
            self.match("COLON")
            points.append(self.parse_precedence_6())
        assert 1 <= len(points) <= 3

        if len(points) == 1:
            return points[0]
        elif len(points) == 2:
            return Range_Expression(points[0], points[1])
        else:
            return Range_Expression(points[0], points[1], points[2])

    # 8. Less than (<), less than or equal to (<=), greater than (>),
    #    greater than or equal to (>=), equal to (==), not equal to (~=)
    def parse_precedence_8(self):
        rv = self.parse_range_expression()

        while self.peek("OPERATOR") and self.nt.value() in ("<", "<=",
                                                            ">", ">=",
                                                            "==", "~="):
            self.match("OPERATOR")
            t_op = self.ct
            rhs = self.parse_range_expression()
            rv = Binary_Operation(8, t_op, rv, rhs)

        return rv

    # 9. Element-wise AND (&)
    def parse_precedence_9(self):
        rv = self.parse_precedence_8()

        while self.peek("OPERATOR", "&"):
            self.match("OPERATOR", "&")
            t_op = self.ct
            rhs = self.parse_precedence_8()
            rv = Binary_Operation(9, t_op, rv, rhs)

        return rv

    # 10. Element-wise OR (|)
    def parse_precedence_10(self):
        rv = self.parse_precedence_9()

        while self.peek("OPERATOR", "|"):
            self.match("OPERATOR", "|")
            t_op = self.ct
            rhs = self.parse_precedence_9()
            rv = Binary_Operation(10, t_op, rv, rhs)

        return rv

    # 11. Short-circuit AND (&&)
    def parse_precedence_11(self):
        rv = self.parse_precedence_10()

        while self.peek("OPERATOR", "&&"):
            self.match("OPERATOR", "&&")
            t_op = self.ct
            rhs = self.parse_precedence_10()
            rv = Binary_Operation(11, t_op, rv, rhs)

        return rv

    # 12. Short-circuit OR (||)
    def parse_precedence_12(self):
        rv = self.parse_precedence_11()

        while self.peek("OPERATOR", "||"):
            self.match("OPERATOR", "||")
            t_op = self.ct
            rhs = self.parse_precedence_11()
            rv = Binary_Operation(12, t_op, rv, rhs)

        return rv

    def parse_matrix_row(self, required_elements=None):
        assert required_elements is None or required_elements >= 1
        rv = []

        if required_elements:
            for i in range(required_elements):
                if i > 0:
                    self.match("COMMA")
                rv.append(self.parse_expression())
        else:
            while not (self.peek("SEMICOLON") or
                       self.peek("NEWLINE") or
                       self.peek("M_KET")):
                rv.append(self.parse_expression())
                if self.peek("COMMA"):
                    self.match("COMMA")

        return rv

    def parse_matrix(self):
        self.match("M_BRA")
        t_open = self.ct

        rows = [self.parse_matrix_row()]
        if self.peek("SEMICOLON"):
            self.match("SEMICOLON")
        if self.peek("NEWLINE"):
            self.match("NEWLINE")
        dim_x = len(rows[0])

        while not (self.peek("SEMICOLON") or
                   self.peek("NEWLINE") or
                   self.peek("M_KET")):
            rows.append(self.parse_matrix_row(dim_x))
            if self.peek("SEMICOLON"):
                self.match("SEMICOLON")
            if self.peek("NEWLINE"):
                self.match("NEWLINE")

        self.match("M_KET")
        t_close = self.ct

        rv = Matrix_Expression(t_open, t_close, rows)
        return rv

    def parse_argument_list(self):
        args = []
        self.match("BRA")
        if self.peek("KET"):
            self.match("KET")
            return args

        while True:
            args.append(self.parse_expression())
            if self.peek("COMMA"):
                self.match("COMMA")
            else:
                break
        self.match("KET")
        return args

    def parse_if_statement(self):
        actions = []

        self.match("KEYWORD", "if")
        t_kw = self.ct
        n_expr = self.parse_expression()
        self.match("NEWLINE")
        n_body = self.parse_delimited_input()
        actions.append((t_kw, n_expr, n_body))

        while self.peek("KEYWORD", "elseif"):
            self.match("KEYWORD", "elseif")
            t_kw = self.ct
            n_expr = self.parse_expression()
            self.match("NEWLINE")
            n_body = self.parse_delimited_input()
            actions.append((t_kw, n_expr, n_body))

        if self.peek("KEYWORD", "else"):
            self.match("KEYWORD", "else")
            t_kw = self.ct
            self.match("NEWLINE")
            n_body = self.parse_delimited_input()
            actions.append((t_kw, None, n_body))

        self.match("KEYWORD", "end")
        self.match("NEWLINE")

        return If_Statement(actions)

    def parse_return_statement(self):
        self.match("KEYWORD", "return")
        t_kw = self.ct
        if self.peek("SEMICOLON"):
            self.match("SEMICOLON")
        self.match("NEWLINE")

        return Return_Statement(t_kw)

    def parse_for_statement(self):
        self.match("KEYWORD", "for")
        t_kw = self.ct
        n_ident = self.parse_identifier()
        self.match("ASSIGNMENT")
        n_range = self.parse_range_expression()
        self.match("NEWLINE")

        n_body = self.parse_delimited_input()

        self.match("KEYWORD", "end")
        self.match("NEWLINE")

        return Simple_For_Statement(t_kw, n_ident, n_range, n_body)

    def parse_while_statement(self):
        self.match("KEYWORD", "while")
        t_kw = self.ct

        n_guard = self.parse_expression()
        self.match("NEWLINE")

        n_body = self.parse_delimited_input()
        self.match("KEYWORD", "end")

        self.match("NEWLINE")

        return While_Statement(t_kw, n_guard, n_body)


def sanity_test(filename):
    try:
        mh.register_file(filename)
        parser = MATLAB_Parser(MATLAB_Lexer(filename))
        parser.parse_file_input()
        print("%s: parsed OK" % filename)
    except Error:
        pass


if __name__ == "__main__":
    # pylint: disable=invalid-name
    from argparse import ArgumentParser
    ap = ArgumentParser()
    ap.add_argument("file")
    options = ap.parse_args()

    sanity_test(options.file)

    mh.print_summary_and_exit()
