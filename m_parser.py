#!/usr/bin/env python3
##############################################################################
##                                                                          ##
##          MATLAB Independent, Small & Safe, High Integrity Tools          ##
##                                                                          ##
##              Copyright (C) 2019-2020, Florian Schanda                    ##
##              Copyright (C) 2019-2020, Zenuity AB                         ##
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

import os
import traceback

from m_lexer import Token_Generator, MATLAB_Lexer, Token_Buffer
from errors import ICE, Error, Location, Message_Handler
import config
from m_ast import *


IGNORED_TOKENS = frozenset(["COMMENT"])


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
    def __init__(self, mh, lexer, cfg):
        assert isinstance(mh, Message_Handler)
        assert isinstance(lexer, Token_Generator)
        assert isinstance(cfg, dict)

        self.lexer = lexer
        self.mh    = mh
        self.cfg   = cfg

        self.context = []

        self.functions_require_end = False
        # If true, we have encountered a function with end. This means
        # all of them must have an end.

        # pylint: disable=invalid-name
        self.ct = None
        self.nt = None
        self.nnt = None
        # pylint: enable=invalid-name

        self.debug_tree = False

        self.next()
        self.next()

    def push_context(self, kind):
        assert kind in ("function", "classdef",
                        "loop", "if", "switch",
                        "block")
        self.context.append(kind)

    def pop_context(self):
        if self.context:
            self.context.pop()
        else:
            raise ICE("context is empty")

    def in_context(self, kind):
        assert kind in ("function", "classdef",
                        "loop", "if", "switch",
                        "block")
        for k in reversed(self.context):
            if kind == k:
                return True
            elif k in ("function", "classdef"):
                return False
        return False

    def next(self):
        self.ct = self.nt
        self.nt = self.nnt
        self.nnt = self.lexer.token()

        while self.nnt:
            # Skip comments and continuations
            while self.nnt and self.nnt.kind in ("COMMENT", "CONTINUATION"):
                self.nnt = self.lexer.token()

            # Join new-lines
            if (self.nnt and
                self.nt and
                self.nnt.kind == "NEWLINE" and
                self.nt.kind == "NEWLINE"):
                self.nnt = self.lexer.token()
            else:
                break

    def match(self, kind, value=None):
        assert kind in TOKEN_KINDS
        self.next()
        if self.ct is None:
            self.mh.error(Location(self.lexer.filename),
                          "expected %s, reached EOF instead" % kind)
        elif self.ct.kind != kind:
            if value:
                self.mh.error(self.ct.location,
                              "expected %s(%s), found %s instead" %
                              (kind, value, self.ct.kind))
            else:
                self.mh.error(self.ct.location,
                              "expected %s, found %s instead" % (kind,
                                                                 self.ct.kind))

        elif value and self.ct.value != value:
            self.mh.error(self.ct.location,
                          "expected %s(%s), found %s(%s) instead" %
                          (kind, value, self.ct.kind, self.ct.value))

    def match_eof(self):
        self.next()
        if self.ct is not None:
            self.mh.error(self.ct.location,
                          "expected end of file, found %s instead" %
                          self.ct.kind)

    def peek(self, kind, value=None):
        assert kind in TOKEN_KINDS
        if self.nt and self.nt.kind == kind:
            if value is None:
                return True
            else:
                return self.nt.value == value
        else:
            return False

    def peek2(self, kind, value=None):
        assert kind in TOKEN_KINDS
        if self.nnt and self.nnt.kind == kind:
            if value is None:
                return True
            else:
                return self.nnt.value == value
        else:
            return False

    def peek_eof(self):
        return self.nt is None

    ##########################################################################
    # Parsing

    def peek_eos(self):
        return self.peek("SEMICOLON") or \
            self.peek("COMMA") or \
            self.peek("NEWLINE")

    def match_eos(self, n_ast, semi = "", allow_nothing = False):
        # This matches end-of-statements (COMMA, SEMICOLON, NEWLINE,
        # EOF). Later for style checking - if semi is ; then it
        # expects a single semicolon, if "" then the preferred form is
        # none.
        #
        # Links everything except the newlines to the given node.
        assert isinstance(n_ast, Node)
        assert semi in ("", ";")

        found_semi_before_nl = False
        found_nl = False
        found_eos = False

        ending_token = self.ct
        # The last token of the previous thing. We might need it later
        # to attach error messages or to record autofix instructions.

        eos_token = None
        # The first comma or semicolon. We might need it later for
        # autofixing.

        # Skip any number of semicolons or commas
        terminator_count = 0
        while self.peek("SEMICOLON") or self.peek("COMMA"):
            terminator_count += 1
            self.nt.set_ast(n_ast)

            if self.peek("SEMICOLON"):
                found_semi_before_nl = True
            elif self.peek("COMMA") and \
                 config.active(self.cfg, "end_of_statements"):
                self.mh.style_issue(self.nt.location,
                                    "end statement with a semicolon"
                                    " instead of comma",
                                    True)
                self.nt.fix["change_to_semicolon"] = True

            if config.active(self.cfg, "end_of_statements") and \
               terminator_count > 1:
                self.mh.style_issue(self.nt.location,
                                    "use only one statement terminator",
                                    True)
                self.nt.fix["delete"] = True

            found_eos = True
            if not eos_token:
                eos_token = self.nt
                if config.active(self.cfg, "end_of_statements"):
                    if semi and eos_token.kind == "COMMA":
                        eos_token.fix["change_to_semicolon"] = True
                    elif not semi:
                        eos_token.fix["delete"] = True

            self.next()

        if config.active(self.cfg, "end_of_statements"):
            if semi and not found_semi_before_nl and not found_eos:
                self.mh.style_issue(ending_token.location,
                                    "end statement with a semicolon",
                                    True)
                ending_token.fix["add_semicolon_after"] = True
            elif not semi and found_semi_before_nl:
                self.mh.style_issue(ending_token.location,
                                    "end this with just a newline",
                                    False)

        # Skip any number of semicolons, commas or newlines
        while self.peek_eos():
            if self.peek("NEWLINE"):
                if found_nl:
                    self.nt.fix["delete"] = True
                found_nl = True
            else:
                self.nt.set_ast(n_ast)

                if config.active(self.cfg, "end_of_statements"):
                    self.mh.style_issue(self.nt.location,
                                        "trailing statement terminator after"
                                        " newline",
                                        True)
                    self.nt.fix["delete"] = True

            found_eos = True
            self.next()

        if config.active(self.cfg, "end_of_statements") and \
           not found_nl:
            self.mh.style_issue(self.ct.location
                                if found_eos
                                else ending_token.location,
                                "end statement with a newline",
                                False)

            # pylint: disable=simplifiable-if-statement
            if eos_token:
                eos_token.fix["add_newline"] = True
            else:
                ending_token.fix["add_newline"] = True
            # pylint: enable=simplifiable-if-statement

        # If we found the end of the file, then this is also an
        # acceptable end of statement
        if self.peek_eof():
            found_eos = True

        if not found_eos and not allow_nothing:
            self.mh.error(self.nt.location,
                          "expected end of statement, found %s instead" %
                          self.nt.kind)
        elif not found_nl and eos_token and not allow_nothing:
            # Workaround for #92 until we can add newlines and indent
            # correctly.
            eos_token.fix["delete"] = False

    def parse_identifier(self, allow_void):
        # identifier ::= <IDENTIFIER>
        #
        # void_or_identifier ::= identifier
        #                      | '~'
        if self.peek("OPERATOR", "~") and allow_void:
            self.match("OPERATOR")
            return Identifier(self.ct)
        elif self.peek("KEYWORD", "end"):
            self.match("KEYWORD", "end")
            return Identifier(self.ct)
        else:
            self.match("IDENTIFIER")
            return Identifier(self.ct)

    def parse_name(self, allow_void):
        # superclass_ref ::= simple_name '@' function_reference
        #
        # simple_name ::= identifier
        #               | simple_name '.' identifier
        #
        # function_reference ::= simple_name
        #                      | simple_name '(' expression_list ')'
        #
        # name ::= superclass_ref
        #        | simple_name
        #        | name '.' identifier
        #        | name '.' '(' expression ')'
        #        | name '(' expression_list ')'
        #        | name '{' expression_list '}'
        #
        # Note that we can only resolve the ambiguity between
        # metaclass, simplename or any of the others late (but we can
        # always resolve it).
        #
        # expression_list ::= <>
        #                   | expression { ',' expression }

        # First we parse as much as possible as a simple name.
        rv = self.parse_simple_name(allow_void)

        # Then we can see if we have a superclass reference. What
        # follows are pretty different parse rules for the two cases.

        if self.peek("AT"):
            self.match("AT")
            t_at = self.ct
            at_prefix = rv
            at_suffix = self.parse_simple_name()
            if self.peek("BRA"):
                at_suffix = Reference(at_suffix)
                at_suffix.set_arguments(self.parse_argument_list(at_suffix))

            return Superclass_Reference(t_at, at_prefix, at_suffix)

        else:
            while (self.peek("SELECTION") or
                   self.peek("BRA") or
                   self.peek("C_BRA")):
                if self.peek("SELECTION"):
                    self.match("SELECTION")
                    tok = self.ct

                    if self.peek("BRA"):
                        self.match("BRA")
                        t_open = self.ct
                        dyn_field = self.parse_expression()
                        self.match("KET")
                        t_close = self.ct
                        rv = Dynamic_Selection(tok, rv, dyn_field)
                        t_open.set_ast(rv)
                        t_close.set_ast(rv)
                    else:
                        field = self.parse_identifier(allow_void=False)
                        rv = Selection(tok, rv, field)
                elif self.peek("BRA"):
                    rv = Reference(rv)
                    rv.set_arguments(self.parse_argument_list(rv))
                elif self.peek("C_BRA"):
                    rv = self.parse_cell_reference(rv)
                else:
                    raise ICE("impossible path (nt.kind = %s)" % self.nt.kind)

            return rv

    def parse_simple_name(self, allow_void=False):
        # reference ::= identifier
        #             | reference '.' identifier

        rv = self.parse_identifier(allow_void=allow_void)

        # We need to lookahead 2 here to avoid parsing dynamic fields

        while self.peek("SELECTION") and not self.peek2("BRA"):
            if self.peek("SELECTION"):
                self.match("SELECTION")
                tok = self.ct
                field = self.parse_identifier(allow_void=allow_void)
                rv = Selection(tok, rv, field)
            else:
                raise ICE("impossible path (nt.kind = %s)" % self.nt.kind)

        return rv

    def parse_file(self):
        # This is the top-level parse function. First we need to
        # figure out exactly what kind of file we're dealing
        # with. This also hilariously depends on the file name.
        while self.peek("NEWLINE"):
            self.next()

        if self.peek("KEYWORD", "function"):
            cunit = Function_File(os.path.basename(self.lexer.filename),
                                  self.parse_function_list(),
                                  self.lexer.in_class_directory)
        elif self.peek("KEYWORD", "classdef") or self.lexer.in_class_directory:
            cunit = self.parse_class_file()
        else:
            cunit = self.parse_script_file()

        if self.debug_tree:
            cunit.debug_parse_tree()

        self.match_eof()

        return cunit

    def parse_script_file(self):
        statements = []
        while not self.peek_eof():
            if self.peek("KEYWORD", "function"):
                break
            else:
                statements.append(self.parse_statement())

        functions = self.parse_function_list()

        rv = Script_File(os.path.basename(self.lexer.filename),
                         Sequence_Of_Statements(statements),
                         functions)

        return rv

    def parse_class_file(self):
        self.functions_require_end = True

        n_classdef  = self.parse_classdef()
        l_functions = self.parse_function_list()

        rv = Class_File(os.path.basename(self.lexer.filename),
                        n_classdef,
                        l_functions)
        return rv

    def parse_function_list(self):
        l_functions = []
        while self.peek("KEYWORD", "function"):
            l_functions.append(self.parse_function_def())

        if not self.functions_require_end and l_functions:
            if len(l_functions) > 1:
                raise ICE("logic error")
            l_functions = self.reorder_as_function_list(l_functions[0])

        return l_functions

    def reorder_as_function_list(self, n_fdef):
        # To deal with the special case where none of the functions
        # are terminated by end we need to flatten out the list we
        # have.
        assert isinstance(n_fdef, Function_Definition)
        functions = []

        while n_fdef:
            functions.append(n_fdef)
            if len(n_fdef.l_nested) == 1:
                n_fdef = n_fdef.l_nested.pop()
            elif len(n_fdef.l_nested) > 1:
                raise ICE("logic error")
            else:
                break

        return functions

    def parse_function_signature(self):
        rv = Function_Signature()

        # Parse returns. Either 'x' or a list '[x, y]'
        l_outputs = []
        if self.peek("A_BRA"):
            out_brackets = True
            self.match("A_BRA")
            self.ct.set_ast(rv)
            if self.peek("A_KET"):
                self.match("A_KET")
                self.ct.set_ast(rv)
            else:
                while True:
                    l_outputs.append(self.parse_identifier(allow_void=True))
                    if self.peek("COMMA"):
                        self.match("COMMA")
                        self.ct.set_ast(rv)
                    else:
                        break
                self.match("A_KET")
                self.ct.set_ast(rv)

        else:
            out_brackets = False
            l_outputs.append(self.parse_simple_name())

        if self.peek("BRA") and len(l_outputs) == 1 and not out_brackets:
            # This is a function that doesn't return anything, so
            # function foo(...
            n_name = l_outputs[0]
            l_outputs = []

        elif self.peek("NEWLINE") and len(l_outputs) == 1 and not out_brackets:
            # As above, but without the brackets
            n_name = l_outputs[0]
            l_outputs = []

        else:
            # This is a normal function, so something like
            # function [a, b] = potato...
            # function a = potato...
            self.match("ASSIGNMENT")
            self.ct.set_ast(rv)
            n_name = self.parse_simple_name()

        l_inputs = []
        if self.peek("BRA"):
            self.match("BRA")
            self.ct.set_ast(rv)
            if self.peek("KET"):
                self.match("KET")
                self.ct.set_ast(rv)
            else:
                while True:
                    l_inputs.append(self.parse_identifier(allow_void=True))
                    if self.peek("COMMA"):
                        self.match("COMMA")
                        self.ct.set_ast(rv)
                    else:
                        break
                self.match("KET")
                self.ct.set_ast(rv)

        rv.set_name(n_name)
        rv.set_inputs(l_inputs)
        rv.set_outputs(l_outputs)
        self.match_eos(rv)
        return rv

    def parse_function_def(self):
        self.match("KEYWORD", "function")
        self.push_context("function")
        t_fun = self.ct

        n_sig = self.parse_function_signature()

        l_body = []
        l_nested = []
        l_argval = []

        # First, deal with any argument validation blocks
        while self.peek("KEYWORD", "arguments"):
            l_argval.append(self.parse_validation_block())

        # Then, process the rest of the function
        while not self.peek("KEYWORD", "end") and not self.peek_eof():
            item = self.parse_statement()
            if isinstance(item, Function_Definition):
                l_nested.append(item)
            else:
                l_body.append(item)

        rv = Function_Definition(t_fun, n_sig,
                                 l_argval,
                                 Sequence_Of_Statements(l_body),
                                 l_nested)

        if self.peek_eof() and self.functions_require_end:
            self.mh.error(t_fun.location,
                          "this function must be terminated with end")
        elif self.peek_eof():
            # TODO: style issue
            pass
        else:
            self.functions_require_end = True
            self.match("KEYWORD", "end")
            self.ct.set_ast(rv)
            self.match_eos(rv)

        self.pop_context()

        return rv

    def parse_name_value_pair_list(self, n_ast):
        assert isinstance(n_ast, Node)

        properties = []

        if self.peek("BRA"):
            self.match("BRA")
            self.ct.set_ast(n_ast)
            while True:
                n_pair = Name_Value_Pair(
                    self.parse_identifier(allow_void=False))

                if self.peek("ASSIGNMENT"):
                    self.match("ASSIGNMENT")
                    t_eq = self.ct
                    n_value = self.parse_expression()
                    n_pair.set_value(t_eq, n_value)

                properties.append(n_pair)

                if self.peek("COMMA"):
                    self.match("COMMA")
                    self.ct.set_ast(n_ast)
                else:
                    break
            self.match("KET")
            self.ct.set_ast(n_ast)

        return properties

    def parse_validation_block(self):
        # See
        # https://uk.mathworks.com/help/matlab/matlab_oop/validate-property-values.html
        # https://uk.mathworks.com/help/matlab/matlab_oop/property-validator-functions.html
        # https://www.mathworks.com/help/matlab/matlab_prog/function-argument-validation-1.html

        if self.peek("KEYWORD", "arguments"):
            self.match("KEYWORD", "arguments")
        else:
            self.match("KEYWORD", "properties")
        t_kw = self.ct

        rv = Special_Block(t_kw)
        rv.set_attributes(self.parse_name_value_pair_list(rv))
        self.match_eos(rv)

        while not self.peek("KEYWORD", "end"):
            cons = Entity_Constraints()

            # First the name we refer to
            if t_kw.value == "arguments":
                cons.set_name(self.parse_simple_name(allow_void=True))
            else:
                cons.set_name(self.parse_identifier(allow_void=False))

            # All other validation options are optional. Historically
            # this is likely because the properties block used to be
            # just a list of class properties, and then it evolved
            # (and eventually got re-used in function argument
            # validation blocks).

            # Dimension validation
            val_dim = []
            if self.peek("BRA"):
                self.match("BRA")
                self.ct.set_ast(cons)

                while True:
                    if self.peek("NUMBER"):
                        self.match("NUMBER")
                        val_dim.append(self.ct)
                    elif self.peek("COLON"):
                        self.match("COLON")
                        val_dim.append(self.ct)
                    else:
                        self.mh.error(self.nt.location,
                                      "dimension validation may"
                                      " contain only integral numbers or :")

                    if self.peek("COMMA"):
                        self.match("COMMA")
                        self.ct.set_ast(cons)
                    else:
                        break

                self.match("KET")
                self.ct.set_ast(cons)

            if len(val_dim) == 1:
                self.mh.error(self.ct.location,
                              "in MATLAB dimension constraints must contain"
                              " at least two dimensions",
                              fatal=False)
                # I speculate that the underlying reason is that the
                # MATLAB parser otherwise sees this as a function or
                # index? Reasons very unclear, this is just a wild
                # guess.
            elif len(val_dim) >= 2:
                cons.set_dimension_constraints(val_dim)

            # Class validation
            if self.peek("IDENTIFIER"):
                cons.set_class_constraint(self.parse_simple_name())

            # Function validation
            if self.peek("C_BRA"):
                self.match("C_BRA")
                self.ct.set_ast(cons)

                while True:
                    cons.add_functional_constraint(
                        self.parse_name(allow_void=False))
                    if self.peek("COMMA"):
                        self.match("COMMA")
                        self.ct.set_ast(cons)
                    else:
                        break

                self.match("C_KET")
                self.ct.set_ast(cons)

            # Default value
            if self.peek("ASSIGNMENT"):
                self.match("ASSIGNMENT")
                self.ct.set_ast(cons)
                cons.set_default_value(self.parse_expression())

            self.match_eos(rv)

            rv.add_constraint(cons)

        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)

        return rv

    def parse_class_methods(self):
        # Using:
        # https://www.mathworks.com/help/matlab/matlab_oop/specifying-methods-and-functions.html
        # https://www.mathworks.com/help/matlab/matlab_oop/method-attributes.html
        # https://www.mathworks.com/help/matlab/matlab_oop/methods-in-separate-files.html

        self.match("KEYWORD", "methods")
        t_kw = self.ct

        rv = Special_Block(t_kw)
        rv.set_attributes(self.parse_name_value_pair_list(rv))
        self.match_eos(rv)

        while not self.peek("KEYWORD", "end"):
            if self.peek("KEYWORD", "function"):
                rv.add_method(self.parse_function_def())
            else:
                rv.add_method(self.parse_function_signature())

        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)

        return rv

    def parse_enumeration(self):
        # Using:
        # https://uk.mathworks.com/help/matlab/matlab_oop/enumerations.html

        self.match("KEYWORD", "enumeration")
        t_kw = self.ct

        rv = Special_Block(t_kw)
        self.match_eos(rv)

        while not self.peek("KEYWORD", "end"):
            enum = Class_Enumeration(self.parse_identifier(allow_void=False))

            if self.peek("BRA"):
                self.match("BRA")
                self.ct.set_ast(enum)

                while True:
                    enum.add_argument(self.parse_expression())
                    if self.peek("COMMA"):
                        self.match("COMMA")
                        self.ct.set_ast(enum)
                    else:
                        break

                self.match("KET")
                self.ct.set_ast(enum)

            rv.add_enumeration(enum)

            self.match_eos(rv)

        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)

        return rv

    def parse_class_events(self):
        # Using the syntax described in
        # https://www.mathworks.com/help/matlab/matlab_oop/events-and-listeners.html
        self.match("KEYWORD", "events")
        t_kw = self.ct

        rv = Special_Block(t_kw)
        self.match_eos(rv)

        while not self.peek("KEYWORD", "end"):
            rv.add_event(self.parse_identifier(allow_void=False))
            self.match_eos(rv)

        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)

        return rv

    def parse_classdef(self):
        # Using the syntax described in
        # https://uk.mathworks.com/help/matlab/matlab_oop/user-defined-classes.html
        # https://uk.mathworks.com/help/matlab/matlab_oop/class-components.html

        self.match("KEYWORD", "classdef")
        self.push_context("classdef")
        rv = Class_Definition(self.ct)

        # Class attributes. Ignored for now.
        rv.set_attributes(self.parse_name_value_pair_list(rv))

        # Class name
        rv.set_name(self.parse_identifier(allow_void=False))

        # Inheritance
        l_super = []
        if self.peek("OPERATOR", "<"):
            self.match("OPERATOR", "<")
            self.ct.set_ast(rv)

            while True:
                sc_name = self.parse_simple_name()
                l_super.append(sc_name)

                if self.peek("OPERATOR", "&"):
                    self.match("OPERATOR", "&")
                    self.ct.set_ast(rv)
                else:
                    break
        rv.set_super_classes(l_super)

        self.match_eos(rv)

        while True:
            if self.peek("KEYWORD", "properties"):
                rv.add_block(self.parse_validation_block())
            elif self.peek("KEYWORD", "methods"):
                rv.add_block(self.parse_class_methods())
            elif self.peek("KEYWORD", "events"):
                rv.add_block(self.parse_class_events())
            elif self.peek("KEYWORD", "enumeration"):
                rv.add_block(self.parse_enumeration())
            elif self.peek("KEYWORD", "end"):
                break
            else:
                self.mh.error(self.nt.location,
                              "expected properties|methods|events|enumeration"
                              " inside classdef")

        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)
        self.pop_context()

        return rv

    def parse_delimited_input(self):
        statements = []

        while True:
            if self.peek("KEYWORD") and self.nt.value in ("end",
                                                          "catch",
                                                          "case",
                                                          "otherwise",
                                                          "else",
                                                          "elseif"):
                break
            statements.append(self.parse_statement())

        return Sequence_Of_Statements(statements)

    def parse_statement(self):
        if self.peek("KEYWORD"):
            if self.nt.value == "for":
                return self.parse_for_statement()
            elif self.nt.value == "if":
                return self.parse_if_statement()
            elif self.nt.value == "global":
                return self.parse_global_statement()
            elif self.nt.value == "while":
                return self.parse_while_statement()
            elif self.nt.value == "return":
                return self.parse_return_statement()
            elif self.nt.value == "switch":
                return self.parse_switch_statement()
            elif self.nt.value == "break":
                return self.parse_break_statement()
            elif self.nt.value == "continue":
                return self.parse_continue_statement()
            elif self.nt.value == "import":
                return self.parse_import_statement()
            elif self.nt.value == "try":
                return self.parse_try_statement()
            elif self.nt.value == "persistent":
                return self.parse_persistent_statement()
            elif self.nt.value == "parfor":
                return self.parse_parfor_statement()
            elif self.nt.value == "spmd":
                return self.parse_spmd_statement()
            elif self.nt.value == "function" and self.in_context("function"):
                if self.context[-1] != "function":
                    self.mh.error(self.nt.location,
                                  "nested function cannot appear inside"
                                  " %s" % self.context[-1])
                return self.parse_function_def()
            else:
                self.mh.error(self.nt.location,
                              "expected valid statement,"
                              " found keyword '%s' instead" % self.nt.value)
        elif self.peek("BANG"):
            self.match("BANG")
            t_bang = self.ct
            self.match("NEWLINE")
            return Naked_Expression_Statement(
                Function_Call(Identifier(t_bang),
                              [Char_Array_Literal(t_bang)],
                              "escape"))
        elif self.peek("A_BRA"):
            return self.parse_list_assignment()
        else:
            # This can be one of three things
            # other_stmt ::= reference "=" expr # simple assignment
            #              | reference CARRAY+  # command form
            #              | expression         # naked expression, could be
            #                                   # a call
            rv = self.parse_expression()

            if self.peek("ASSIGNMENT"):
                self.match("ASSIGNMENT")
                t_eq = self.ct
                if not isinstance(rv, Name):
                    self.mh.error(t_eq.location,
                                  "left-hand side of assignment must be a"
                                  " Name, found %s instead" %
                                  rv.__class__.__name__)
                rhs = self.parse_expression()
                if config.active(self.cfg, "builtin_shadow"):
                    rv.sty_check_builtin_shadow(self.mh, self.cfg)
                rv = Simple_Assignment_Statement(t_eq, rv, rhs)

            elif self.peek("CARRAY"):
                # Sanity check that the function is a simple name
                if not isinstance(rv, (Identifier, Selection)):
                    self.mh.error(self.ct.location,
                                  "command form requires a simple (dotted)"
                                  " identifier; found %s instead" %
                                  rv.__class__.__name__)

                arg_list = []
                while self.peek("CARRAY"):
                    self.match("CARRAY")
                    arg_list.append(Char_Array_Literal(self.ct))
                rv = Function_Call(rv, arg_list, "command")
                rv = Naked_Expression_Statement(rv)

            else:
                rv = Naked_Expression_Statement(rv)

            self.match_eos(rv, ";")

            return rv

    def parse_list_assignment(self):
        # Assignment
        #   s_assignee_matrix "=" expr           '[<ref>] = <expr>'
        #   m_assignee_matrix "=" reference      '[<ref>, <ref>] = <ref>'
        #
        # The list cannot be empty, and there can be an optional
        # trailing comma. This is not the same as a function return
        # list, since there cannot be a trailing comma there.

        rv = Compound_Assignment_Statement()

        lhs = []
        require_comma = False

        self.match("A_BRA")
        self.ct.set_ast(rv)
        if self.peek("COMMA"):
            self.match("COMMA")
            self.ct.set_ast(rv)
        while True:
            # There is a special case we need to take care of with
            # ~. There is a MATLAB bug/weirdness with [~ x], which is
            # parsed like [~x], but [x y] is OK for some reason. See
            # issue #70. Hence we enforce commas after any ~.
            if self.peek("OPERATOR", "~"):
                require_comma = True
            target = self.parse_name(allow_void=True)
            if config.active(self.cfg, "builtin_shadow"):
                target.sty_check_builtin_shadow(self.mh, self.cfg)
            lhs.append(target)
            if (self.peek("COMMA") or require_comma) and \
               not self.peek("A_KET"):
                self.match("COMMA")
                self.ct.set_ast(rv)
                require_comma = False
            if self.peek("A_KET"):
                break
        self.match("A_KET")
        self.ct.set_ast(rv)
        rv.set_targets(lhs)

        self.match("ASSIGNMENT")
        rv.set_token_eq(self.ct)

        if len(lhs) == 1:
            # We've got something like
            #    [x] = <expr>
            rhs = self.parse_expression()
        else:
            # We've got something like
            #    [x, y] = fun(...)
            #
            # I believe that this can't be an expression, it basically
            # has to be a function call. Needs to be checked.
            rhs = self.parse_expression()
        rv.set_expression(rhs)

        self.match_eos(rv, ";")
        return rv

    def parse_expression(self):
        return self.parse_precedence_12()

    # 1. Parentheses ()
    def parse_precedence_1(self):
        if self.peek("NUMBER"):
            self.match("NUMBER")
            return Number_Literal(self.ct)

        elif self.peek("CARRAY"):
            self.match("CARRAY")
            return Char_Array_Literal(self.ct)

        elif self.peek("STRING"):
            self.match("STRING")
            return String_Literal(self.ct)

        elif self.peek("BRA"):
            self.match("BRA")
            t_open = self.ct
            expr = self.parse_expression()
            self.match("KET")
            t_close = self.ct
            t_open.set_ast(expr)
            t_close.set_ast(expr)
            return expr

        elif self.peek("M_BRA"):
            return self.parse_matrix()

        elif self.peek("C_BRA"):
            return self.parse_cell()

        elif self.peek("COLON"):
            self.match("COLON")
            return Reshape(self.ct)

        elif self.peek("AT"):
            return self.parse_function_handle()

        elif self.peek("METACLASS"):
            self.match("METACLASS")
            tok = self.ct
            return Metaclass(tok, self.parse_simple_name())

        else:
            return self.parse_name(allow_void=False)

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
    def parse_precedence_2(self):
        # In Octave chaining ^ is left associative, i.e. 2 ^ 3 ^ 2 ==
        # (2 ^ 3) ^ 2 == 64.
        #
        # TODO: Is this also true for MATLAB?
        rv = self.parse_precedence_1()

        while self.peek("OPERATOR") and self.nt.value in ("^", ".^",
                                                          "'", ".'"):
            self.match("OPERATOR")
            t_op = self.ct
            if t_op.value in ("^", ".^"):
                unary_chain = []
                while self.peek("OPERATOR") and \
                      self.nt.value in ("-", "+", "~"):
                    self.match("OPERATOR")
                    unary_chain.append(self.ct)
                rhs = self.parse_precedence_1()
                while unary_chain:
                    rhs = Unary_Operation(3, unary_chain.pop(), rhs)
                rv = Binary_Operation(2, t_op, rv, rhs)
            else:
                rv = Unary_Operation(2, t_op, rv)

        return rv

    def parse_precedence_3(self):
        # This is dealt with as a special case in (2).
        return self.parse_precedence_2()

    # 4. Unary plus (+), unary minus (-), logical negation (~)
    def parse_precedence_4(self):
        if self.peek("OPERATOR") and self.nt.value in ("+", "-", "~"):
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

        while self.peek("OPERATOR") and self.nt.value in ("*", ".*",
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

        while self.peek("OPERATOR") and self.nt.value in ("+", "-"):
            self.match("OPERATOR")
            t_op = self.ct
            rhs = self.parse_precedence_5()
            rv = Binary_Operation(6, t_op, rv, rhs)

        return rv

    # 7. Colon operator (:)
    def parse_range_expression(self):
        t_first_colon = None
        t_second_colon = None
        points = []
        points.append(self.parse_precedence_6())
        if self.peek("COLON"):
            self.match("COLON")
            t_first_colon = self.ct
            points.append(self.parse_precedence_6())
        if self.peek("COLON"):
            self.match("COLON")
            t_second_colon = self.ct
            points.append(self.parse_precedence_6())
        assert 1 <= len(points) <= 3

        if len(points) == 1:
            return points[0]
        elif len(points) == 2:
            return Range_Expression(points[0], t_first_colon, points[1])
        else:
            return Range_Expression(points[0], t_first_colon, points[2],
                                    t_second_colon, points[1])

    # 8. Less than (<), less than or equal to (<=), greater than (>),
    #    greater than or equal to (>=), equal to (==), not equal to (~=)
    def parse_precedence_8(self):
        rv = self.parse_range_expression()

        chain_length = 1
        while self.peek("OPERATOR") and self.nt.value in ("<", "<=",
                                                          ">", ">=",
                                                          "==", "~="):
            chain_length += 1
            self.match("OPERATOR")
            t_op = self.ct
            rhs = self.parse_range_expression()
            rv = Binary_Operation(8, t_op, rv, rhs)

            if chain_length > 2:
                self.mh.warning(t_op.location,
                                "chained relation does not work the"
                                " way you think it does")

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

    def parse_matrix_row(self):
        rv = Row()

        first = True

        while not (self.peek("SEMICOLON") or
                   self.peek("NEWLINE") or
                   self.peek("C_KET") or
                   self.peek("M_KET")):
            if first:
                first = False
                # Very bad style, but you can start a matrix with a
                # comma, e.g. [,1,2] which is the same as [1, 2]
                if self.peek("COMMA"):
                    self.match("COMMA")
                    self.ct.set_ast(rv)

            if (self.peek("SEMICOLON") or
                self.peek("NEWLINE") or
                self.peek("C_KET") or
                self.peek("M_KET")):
                # Bad style, but you can have a trailing comma in your
                # matrix, e.g. [1,2,] which is the same as [1, 2]
                break

            rv.add_item(self.parse_expression())

            if self.peek("SEMICOLON"):
                pass
            elif self.peek("NEWLINE"):
                pass
            elif self.peek("C_KET") or self.peek("M_KET"):
                pass
            else:
                self.match("COMMA")
                self.ct.set_ast(rv)

        return rv

    def parse_matrix(self):
        self.match("M_BRA")
        rv = Matrix_Expression(self.ct)

        # Bad style, but there may be leading semicolons, e.g [;;3]
        # which is the same as [3].
        while self.peek("SEMICOLON"):
            self.match("SEMICOLON")
            self.ct.set_ast(rv)

        if not self.peek("M_KET"):
            rv.add_row(self.parse_matrix_row())
            while self.peek("SEMICOLON"):
                self.match("SEMICOLON")
                self.ct.set_ast(rv)
            if self.peek("NEWLINE"):
                self.match("NEWLINE")

            while not (self.peek("SEMICOLON") or
                       self.peek("NEWLINE") or
                       self.peek("M_KET")):
                rv.add_row(self.parse_matrix_row())
                while self.peek("SEMICOLON"):
                    self.match("SEMICOLON")
                    self.ct.set_ast(rv)
                if self.peek("NEWLINE"):
                    self.match("NEWLINE")

        self.match("M_KET")
        rv.set_closing_bracket(self.ct)
        return rv

    def parse_cell(self):
        self.match("C_BRA")
        rv = Cell_Expression(self.ct)

        # Bad style, but there may be leading semicolons, e.g {;;3}
        # which is the same as {3}.
        while self.peek("SEMICOLON"):
            self.match("SEMICOLON")
            self.ct.set_ast(rv)

        if not self.peek("C_KET"):
            rv.add_row(self.parse_matrix_row())
            while self.peek("SEMICOLON"):
                self.match("SEMICOLON")
                self.ct.set_ast(rv)
            if self.peek("NEWLINE"):
                self.match("NEWLINE")

            while not (self.peek("SEMICOLON") or
                       self.peek("NEWLINE") or
                       self.peek("C_KET")):
                rv.add_row(self.parse_matrix_row())
                while self.peek("SEMICOLON"):
                    self.match("SEMICOLON")
                    self.ct.set_ast(rv)
                if self.peek("NEWLINE"):
                    self.match("NEWLINE")

        self.match("C_KET")
        rv.set_closing_bracket(self.ct)
        return rv

    def parse_function_handle(self):
        self.match("AT")
        t_at = self.ct

        if self.peek("BRA"):
            rv = Lambda_Function(t_at)

            self.match("BRA")
            self.ct.set_ast(rv)

            while not self.peek("KET"):
                rv.add_parameter(self.parse_identifier(allow_void=True))
                if self.peek("COMMA"):
                    self.match("COMMA")
                    self.ct.set_ast(rv)
                else:
                    break

            self.match("KET")
            self.ct.set_ast(rv)

            rv.set_body(self.parse_expression())
            return rv

        else:
            name = self.parse_simple_name()
            return Function_Pointer(t_at, name)

    def parse_argument_list(self, n_ast):
        assert isinstance(n_ast, Node)
        # arglist ::= '(' ')'
        #           | '(' expression { ',' expression } '}'
        #
        # Note: This list can be empty
        args = []
        self.match("BRA")
        self.ct.set_ast(n_ast)
        if self.peek("KET"):
            self.match("KET")
            self.ct.set_ast(n_ast)
            return args

        while True:
            args.append(self.parse_expression())
            if self.peek("COMMA"):
                self.match("COMMA")
                self.ct.set_ast(n_ast)
            elif self.peek("KET"):
                break
        self.match("KET")
        self.ct.set_ast(n_ast)
        return args

    def parse_cell_reference(self, n_name):
        # cell_arglist ::= <<name>> '{' expression { ',' expression } '}'
        #
        # Note: cannot be empty
        rv = Cell_Reference(n_name)

        self.match("C_BRA")
        self.ct.set_ast(rv)

        while True:
            rv.add_argument(self.parse_expression())
            if self.peek("COMMA"):
                self.match("COMMA")
                self.ct.set_ast(rv)
            elif self.peek("C_KET"):
                break

        self.match("C_KET")
        self.ct.set_ast(rv)

        return rv

    def parse_if_statement(self):
        actions = []

        self.match("KEYWORD", "if")
        self.push_context("if")
        n_action = Action(self.ct)
        n_action.set_expression(self.parse_expression())
        self.match_eos(n_action, allow_nothing=True)
        n_action.set_body(self.parse_delimited_input())
        actions.append(n_action)

        while self.peek("KEYWORD", "elseif"):
            self.match("KEYWORD", "elseif")
            n_action = Action(self.ct)
            n_action.set_expression(self.parse_expression())
            self.match_eos(n_action, allow_nothing=True)
            n_action.set_body(self.parse_delimited_input())
            actions.append(n_action)

        if self.peek("KEYWORD", "else"):
            self.match("KEYWORD", "else")
            n_action = Action(self.ct)
            self.match_eos(n_action, allow_nothing=True)
            n_action.set_body(self.parse_delimited_input())
            actions.append(n_action)

        self.match("KEYWORD", "end")
        rv = If_Statement(actions)
        self.ct.set_ast(rv)
        self.match_eos(rv)
        self.pop_context()

        return rv

    def parse_return_statement(self):
        self.match("KEYWORD", "return")
        rv = Return_Statement(self.ct)
        self.match_eos(rv)

        return rv

    def parse_break_statement(self):
        self.match("KEYWORD", "break")
        rv = Break_Statement(self.ct)

        if not self.in_context("loop"):
            self.mh.error(self.ct.location,
                          "break must appear inside loop",
                          fatal = False)

        self.match_eos(rv)

        return rv

    def parse_continue_statement(self):
        self.match("KEYWORD", "continue")
        rv = Continue_Statement(self.ct)

        if not self.in_context("loop"):
            self.mh.error(self.ct.location,
                          "continue must appear inside loop",
                          fatal = False)

        self.match_eos(rv)

        return rv

    def parse_for_assignment(self,
                             n_ast,
                             allow_brackets=False,
                             allow_recursion=False):
        assert isinstance(n_ast, Node)
        # Apparently it's OK to wrap the loop in arbitrarily nested
        # brackets, e.g. for ((i = 1:2))
        #
        # TODO: Flag bad style
        #
        # TODO: In octave we can recurse for the normal for, in MATLAB
        # we cannot. For parfor we can never recurse.
        if self.peek("BRA") and allow_brackets:
            self.match("BRA")
            self.ct.set_ast(n_ast)
            n_ident, n_expr = self.parse_for_assignment(n_ast,
                                                        allow_recursion,
                                                        allow_recursion)
            self.match("KET")
            self.ct.set_ast(n_ast)
        else:
            n_ident = self.parse_identifier(allow_void=False)
            self.match("ASSIGNMENT")
            self.ct.set_ast(n_ast)
            n_expr = self.parse_expression()

        return n_ident, n_expr

    def parse_for_statement(self):
        self.match("KEYWORD", "for")
        self.push_context("loop")
        rv = General_For_Statement(self.ct)

        n_ident, n_expr = self.parse_for_assignment(rv, allow_brackets=True)
        rv.set_ident(n_ident)
        rv.set_expression(n_expr)
        self.match_eos(rv)

        rv.set_body(self.parse_delimited_input())

        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)
        self.pop_context()

        return rv

    def parse_parfor_statement(self):
        self.match("KEYWORD", "parfor")
        self.push_context("loop")
        rv = Parallel_For_Statement(self.ct)

        if self.peek("BRA"):
            # parfor (var = first:last, max_workers)
            self.match("BRA")
            self.ct.set_ast(rv)

            n_ident, n_expr = self.parse_for_assignment(rv)
            if self.peek("COMMA"):
                self.match("COMMA")
                self.ct.set_ast(rv)
                rv.set_workers(self.parse_expression())

            self.match("KET")
            self.ct.set_ast(rv)

        else:
            n_ident, n_expr = self.parse_for_assignment(rv)

        rv.set_ident(n_ident)
        if not isinstance(n_expr, Range_Expression):
            raise ICE("parfor range is not a range")
        else:
            rv.set_range(n_expr)

        self.match_eos(rv)

        rv.set_body(self.parse_delimited_input())

        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)
        self.pop_context()

        return rv

    def parse_while_statement(self):
        self.match("KEYWORD", "while")
        self.push_context("loop")
        t_kw = self.ct
        n_guard = self.parse_expression()
        rv = While_Statement(t_kw, n_guard)
        self.match_eos(rv)

        rv.set_body(self.parse_delimited_input())
        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)
        self.pop_context()

        return rv

    def parse_global_statement(self):
        self.match("KEYWORD", "global")
        rv = Global_Statement(self.ct)

        while True:
            rv.add_name(self.parse_identifier(allow_void=False))
            if self.peek("NEWLINE"):
                self.match("NEWLINE")
                break
            elif self.peek("SEMICOLON"):
                self.match("SEMICOLON")
                self.ct.set_ast(rv)
                self.match("NEWLINE")
                break

        return rv

    def parse_persistent_statement(self):
        self.match("KEYWORD", "persistent")
        rv = Persistent_Statement(self.ct)

        while True:
            rv.add_name(self.parse_identifier(allow_void=False))
            if self.peek_eos():
                self.match_eos(rv)
                break

        return rv

    def parse_switch_statement(self):
        self.match("KEYWORD", "switch")
        self.push_context("switch")
        t_switch = self.ct
        n_switch_expr = self.parse_expression()
        rv = Switch_Statement(t_switch, n_switch_expr)
        self.match_eos(rv)

        while True:
            if self.peek("KEYWORD", "otherwise"):
                self.match("KEYWORD", "otherwise")
                n_action = Action(self.ct)
                self.match_eos(n_action, allow_nothing=True)
                n_action.set_body(self.parse_delimited_input())
                rv.add_action(n_action)
                break
            else:
                self.match("KEYWORD", "case")
                n_action = Action(self.ct)
                n_action.set_expression(self.parse_expression())
                self.match_eos(n_action, allow_nothing=True)
                n_action.set_body(self.parse_delimited_input())
                rv.add_action(n_action)

            if self.peek("KEYWORD", "end"):
                break

        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)
        self.pop_context()

        return rv

    def parse_import_statement(self):
        # https://www.mathworks.com/help/matlab/ref/import.html
        #
        # What makes this one curious is that previously it was
        # probably a command-form function. Now it is a statement. But
        # for this reason (probably) commas are not used to separate
        # the things to import.
        #
        # In MISS_HIT for now you can only import a single name per
        # import statement (i.e. no space separated lists allowed
        # here).
        self.match("KEYWORD", "import")
        rv = Import_Statement(self.ct)

        self.match("IDENTIFIER")
        chain = [self.ct]
        while self.peek("SELECTION") or self.peek("OPERATOR", ".*"):
            if self.peek("OPERATOR", ".*"):
                self.match("OPERATOR", ".*")
                chain.append(self.ct)
                break
            else:
                self.match("SELECTION")
                self.ct.set_ast(rv)
                self.match("IDENTIFIER")
                chain.append(self.ct)

        rv.set_chain(chain)
        self.match_eos(rv)

        return rv

    def parse_try_statement(self):
        self.match("KEYWORD", "try")
        self.push_context("block")
        rv = Try_Statement(self.ct)
        self.match_eos(rv, allow_nothing=True)

        rv.set_body(self.parse_delimited_input())

        if self.peek("KEYWORD", "end"):
            # A missing catch block seems to be an undocumented
            # extension to MATLAB that Octave also supports. It should
            # be equivalent to a general catch with an empty body.
            pass

        else:
            self.match("KEYWORD", "catch")
            t_catch = self.ct
            if not self.peek_eos():
                rv.set_ident(self.parse_identifier(allow_void = False))
            self.match_eos(rv)

            rv.set_handler_body(t_catch, self.parse_delimited_input())

        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)
        self.pop_context()

        return rv

    def parse_spmd_statement(self):
        self.match("KEYWORD", "spmd")
        self.push_context("block")
        rv = SPMD_Statement(self.ct)
        self.match_eos(rv)

        rv.set_body(self.parse_delimited_input())
        self.match("KEYWORD", "end")
        self.ct.set_ast(rv)
        self.match_eos(rv)
        self.pop_context()

        return rv


def sanity_test(mh, filename, show_bt, show_tree, show_dot, show_cfg):
    # pylint: disable=import-outside-toplevel
    import g_cfg
    # pylint: enable=import-outside-toplevel

    try:
        mh.register_file(filename)
        lexer = MATLAB_Lexer(mh, filename)
        tbuf = Token_Buffer(lexer, config.BASE_CONFIG)
        parser = MATLAB_Parser(mh,
                               tbuf,
                               config.BASE_CONFIG)
        parser.debug_tree = show_dot
        tree = parser.parse_file()
        if show_tree:
            print("-" * 70)
            print("--  Parse tree for %s" % os.path.basename(filename))
            tree.pp_node()
            print("-" * 70)
        if show_cfg:
            if isinstance(tree, Script_File):
                g_cfg.build_cfg(tree)

        tbuf.debug_validate_links()

    except Error:
        if show_bt:
            traceback.print_exc()

    except ICE as ice:
        if show_bt:
            traceback.print_exc()
        print("ICE:", ice.reason)

    mh.finalize_file(filename)


def parser_test_main():
    from argparse import ArgumentParser
    ap = ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--no-tb",
                    action="store_true",
                    default=False,
                    help="Do not show debug-style backtrace")
    ap.add_argument("--tree",
                    action="store_true",
                    default=False,
                    help="Print text-based parse tree")
    ap.add_argument("--dot",
                    action="store_true",
                    default=False,
                    help="Create parse tree with graphviz for each function")
    ap.add_argument("--cfg",
                    action="store_true",
                    default=False,
                    help="Create cfg with graphviz for each function")
    options = ap.parse_args()

    mh = Message_Handler()
    mh.sort_messages = False
    mh.colour = False

    sanity_test(mh, options.file,
                not options.no_tb,
                options.tree,
                options.dot,
                options.cfg)

    mh.summary_and_exit()


if __name__ == "__main__":
    parser_test_main()
