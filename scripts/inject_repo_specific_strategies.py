#!/usr/bin/env python3
"""Inject repo-specific strategies extracted from v3 successful runs into the graph.

These strategies were derived from analyzing the git diffs of 25 resolved
SWE-bench problems in the fifty_v3 experiment. Each strategy captures a
concrete, repo-specific fix pattern that can transfer to similar problems
in the same repository.
"""

import os
import sys

from agent_memory.neo4j_store import Neo4jStore
from agent_memory.embeddings import get_embedding_client

# 25 repo-specific strategies extracted from v3 resolved diffs
STRATEGIES = [
    # astropy__astropy-12907: _cstack assigned 1 instead of right matrix
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In astropy's modeling/separable.py, the _cstack function for "
            "building separability matrices of nested compound models must "
            "assign the actual sub-model's coordinate matrix (right) to "
            "cright, not the constant 1. When the right operand is already "
            "a matrix, use cright[...] = right instead of cright[...] = 1."
        ),
        "category": "astropy_modeling",
        "repo": "astropy/astropy",
        "instance_id": "astropy__astropy-12907",
    },
    # astropy__astropy-14539: FITS diff missed Q-format variable-length columns
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In astropy's io/fits/diff.py, the TableDataDiff comparison of "
            "variable-length array columns only checks for 'P' format code "
            "but FITS also uses 'Q' format for 64-bit heap pointers. Fix: "
            "change the check from '\"P\" in col.format' to "
            "'any(code in col.format for code in (\"P\", \"Q\"))' to handle "
            "both variable-length column descriptors."
        ),
        "category": "astropy_fits",
        "repo": "astropy/astropy",
        "instance_id": "astropy__astropy-14539",
    },
    # django__django-11490: union().values_list() mutates original query
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's db/models/sql/compiler.py, when a union/intersect "
            "combined query applies values_select to sub-compilers, it must "
            "clone the sub-query first to avoid mutating the original "
            "QuerySet. Before calling compiler.query.set_values(), do: "
            "compiler = compiler.query.clone().get_compiler(self.using, "
            "self.connection). This prevents repeated .values_list() calls "
            "on the same union QuerySet from returning wrong columns."
        ),
        "category": "django_orm",
        "repo": "django/django",
        "instance_id": "django__django-11490",
    },
    # django__django-12050: resolve_lookup_value converts list to tuple
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's db/models/sql/query.py, the resolve_lookup_value "
            "method wraps resolved values with tuple() unconditionally. "
            "This breaks __in lookups that pass a list, because the "
            "original container type is lost. Fix: use type(value)(...) "
            "instead of tuple(...) to preserve the original iterable type "
            "(list stays list, tuple stays tuple)."
        ),
        "category": "django_orm",
        "repo": "django/django",
        "instance_id": "django__django-12050",
    },
    # django__django-12155: trim_docstring includes first line in indent calc
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's contrib/admindocs/utils.py, the trim_docstring "
            "function computes minimum indentation across ALL lines "
            "including the first. Per PEP 257, the first line has no "
            "leading indent. Fix: compute indent from lines[1:] instead "
            "of lines, so docstrings with content on the first line are "
            "trimmed correctly."
        ),
        "category": "django_utils",
        "repo": "django/django",
        "instance_id": "django__django-12155",
    },
    # django__django-12419: SECURE_REFERRER_POLICY default was None
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's conf/global_settings.py, the default value for "
            "SECURE_REFERRER_POLICY should be 'same-origin' (not None). "
            "When fixing security middleware defaults, also update the "
            "check_framework security tests and middleware tests to "
            "verify the new default value is applied automatically."
        ),
        "category": "django_security",
        "repo": "django/django",
        "instance_id": "django__django-12419",
    },
    # django__django-13933: ModelChoiceField error missing %(value)s
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's forms/models.py, ModelChoiceField's "
            "invalid_choice error message should include %(value)s so "
            "users see which value was invalid. Two changes needed: "
            "(1) update the default_error_messages string to use "
            "'%(value)s is not one of', and (2) pass params={'value': "
            "value} to the ValidationError constructor in to_python()."
        ),
        "category": "django_forms",
        "repo": "django/django",
        "instance_id": "django__django-13933",
    },
    # django__django-14373: Year format Y doesn't zero-pad before 1000
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's utils/dateformat.py, the Y() method returns "
            "self.data.year as an integer, which omits leading zeros "
            "for years before 1000 (e.g., year 1 returns '1' not "
            "'0001'). Fix: return '%04d' % self.data.year to ensure "
            "4-digit zero-padded year output."
        ),
        "category": "django_utils",
        "repo": "django/django",
        "instance_id": "django__django-14373",
    },
    # django__django-14725: ModelFormSet lacks edit_only option
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's forms/models.py, to add an edit_only option to "
            "BaseModelFormSet that prevents creating new objects from "
            "extra forms: (1) add edit_only=False class attribute, "
            "(2) in save_new_objects() return early if self.edit_only, "
            "(3) thread edit_only through modelformset_factory and "
            "inlineformset_factory function signatures."
        ),
        "category": "django_forms",
        "repo": "django/django",
        "instance_id": "django__django-14725",
    },
    # django__django-15375: Aggregate default loses is_summary on Coalesce
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's db/models/aggregates.py, when an Aggregate with "
            "a default value wraps itself in Coalesce during "
            "resolve_expression, the is_summary attribute is not "
            "propagated. This causes 'aggregate must be used in an "
            "annotation' errors when filtering after annotation. Fix: "
            "after creating the Coalesce wrapper, set "
            "coalesce.is_summary = c.is_summary."
        ),
        "category": "django_orm",
        "repo": "django/django",
        "instance_id": "django__django-15375",
    },
    # django__django-15382: Exists with empty queryset raises EmptyResultSet
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's db/models/expressions.py, the Exists subquery "
            "crashes with EmptyResultSet when given an empty queryset "
            "(e.g., Model.objects.none()) in a filter. Fix: (1) set "
            "empty_result_set_value = False on the Exists class, and "
            "(2) in as_sql(), catch EmptyResultSet and return "
            "compiler.compile(Value(bool(self.negated))) or the integer "
            "equivalent for backends without boolean select support."
        ),
        "category": "django_orm",
        "repo": "django/django",
        "instance_id": "django__django-15382",
    },
    # django__django-15741: lazy string as date format crashes get_format
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's utils/formats.py, the get_format function fails "
            "when passed a lazy string (e.g., gettext_lazy('Y-m-d')) as "
            "the format_type because it's used as a dict key and cache "
            "key without being resolved. Fix: add format_type = "
            "force_str(format_type) at the start of get_format()."
        ),
        "category": "django_utils",
        "repo": "django/django",
        "instance_id": "django__django-15741",
    },
    # django__django-16595: AlterField + AlterField doesn't reduce
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's db/migrations/operations/fields.py, the "
            "AlterField.reduce() method handles RemoveField and "
            "RenameField but not consecutive AlterField operations on "
            "the same field. Fix: add a check at the top of reduce() "
            "for isinstance(operation, AlterField) and "
            "self.is_same_field_operation(operation), returning "
            "[operation] to collapse multiple AlterFields into the "
            "latest one."
        ),
        "category": "django_migrations",
        "repo": "django/django",
        "instance_id": "django__django-16595",
    },
    # django__django-16631: get_user doesn't check SECRET_KEY_FALLBACKS
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's contrib/auth/__init__.py, the get_user() "
            "function only validates the session hash against the "
            "current SECRET_KEY. After SECRET_KEY rotation, all sessions "
            "are invalidated. Fix: when session_hash_verified is False, "
            "iterate over user.get_session_auth_fallback_hash() (which "
            "yields hashes from SECRET_KEY_FALLBACKS) and cycle the "
            "session key on match. In base_user.py, add "
            "get_session_auth_fallback_hash() and refactor "
            "get_session_auth_hash() to use a shared _get_session_auth_hash(secret=None)."
        ),
        "category": "django_auth",
        "repo": "django/django",
        "instance_id": "django__django-16631",
    },
    # django__django-17087: nested class method serialization uses __name__
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Django's db/migrations/serializer.py, "
            "FunctionTypeSerializer uses klass.__name__ to build the "
            "import path for bound class methods. For nested classes "
            "(e.g., TestModel1.Capability.default), __name__ gives only "
            "'Capability', missing the outer class. Fix: use "
            "klass.__qualname__ instead of klass.__name__ to get the "
            "full dotted path (e.g., 'TestModel1.Capability')."
        ),
        "category": "django_migrations",
        "repo": "django/django",
        "instance_id": "django__django-17087",
    },
    # matplotlib__matplotlib-20488: LogNorm image with vmin=0 mishandled
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In matplotlib's image.py, the _ImageBase._make_image method "
            "handles LogNorm resampling bounds incorrectly: it checks "
            "'s_vmin < 0' but LogNorm requires s_vmin > 0. A value of "
            "exactly 0 passes the check but is invalid for log scale. "
            "Fix: change the condition to 's_vmin <= 0' and use "
            "np.finfo(scaled_dtype).tiny (smallest normal float) "
            "instead of .eps to avoid collapsing large dynamic ranges."
        ),
        "category": "matplotlib_image",
        "repo": "matplotlib/matplotlib",
        "instance_id": "matplotlib__matplotlib-20488",
    },
    # matplotlib__matplotlib-25122: spectral helper uses abs(window).sum()
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In matplotlib's mlab.py, the _spectral_helper function "
            "computes np.abs(window).sum() multiple times for magnitude "
            "and complex mode scaling. For windows with negative values "
            "(e.g., flat-top windows), abs(window).sum() != window.sum() "
            "and gives incorrect amplitude normalization. Fix: compute "
            "window_sum = window.sum() once before the FFT and use it "
            "for magnitude/complex mode scaling (lines that divide by "
            "np.abs(window).sum())."
        ),
        "category": "matplotlib_mlab",
        "repo": "matplotlib/matplotlib",
        "instance_id": "matplotlib__matplotlib-25122",
    },
    # psf__requests-1142: GET sets Content-Length: 0
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In the requests library's models.py, the "
            "prepare_content_length method unconditionally sets "
            "Content-Length to '0' at the start, then overwrites if "
            "body is not None. This means GET requests (with body=None) "
            "get a Content-Length: 0 header, which some servers reject. "
            "Fix: restructure the method to only set Content-Length when "
            "body is not None, and pop('Content-Length', None) when body "
            "is None."
        ),
        "category": "requests_http",
        "repo": "psf/requests",
        "instance_id": "psf__requests-1142",
    },
    # pydata__xarray-3677: Dataset.merge rejects DataArray
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In xarray's core/merge.py, the dataset_merge_method "
            "function doesn't handle DataArray inputs — passing a "
            "DataArray to Dataset.merge() fails. Fix: at the top of "
            "dataset_merge_method, add: if isinstance(other, DataArray): "
            "other = other.to_dataset(). Import DataArray locally to "
            "avoid circular imports."
        ),
        "category": "xarray_merge",
        "repo": "pydata/xarray",
        "instance_id": "pydata__xarray-3677",
    },
    # pytest-dev__pytest-7571: caplog.set_level doesn't restore handler level
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In pytest's _pytest/logging.py, LogCaptureFixture.set_level "
            "sets both the logger level and handler level but only "
            "restores the logger level in _finalize(). Fix: (1) add "
            "_initial_handler_level = None to __init__, (2) in "
            "set_level(), save self.handler.level on first call, "
            "(3) in _finalize(), restore self.handler.setLevel("
            "self._initial_handler_level) before restoring logger "
            "levels."
        ),
        "category": "pytest_logging",
        "repo": "pytest-dev/pytest",
        "instance_id": "pytest-dev__pytest-7571",
    },
    # scikit-learn__scikit-learn-13328: HuberRegressor fails on bool input
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In scikit-learn's linear_model/huber.py, the HuberRegressor "
            "fit() method calls check_X_y without specifying dtype, so "
            "boolean arrays pass through uncasted. The scipy optimizer "
            "then fails on bool inputs. Fix: add "
            "dtype=[np.float64, np.float32] to the check_X_y call to "
            "force numeric conversion."
        ),
        "category": "sklearn_linear",
        "repo": "scikit-learn/scikit-learn",
        "instance_id": "scikit-learn__scikit-learn-13328",
    },
    # sphinx-doc__sphinx-8475: linkcheck crashes on TooManyRedirects
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Sphinx's builders/linkcheck.py, the HEAD request "
            "error handler only catches HTTPError before falling back "
            "to GET. When a server causes an infinite redirect loop, "
            "requests raises TooManyRedirects which is uncaught. Fix: "
            "change 'except HTTPError' to "
            "'except (HTTPError, TooManyRedirects)' in the HEAD "
            "fallback block and import TooManyRedirects from "
            "requests.exceptions."
        ),
        "category": "sphinx_linkcheck",
        "repo": "sphinx-doc/sphinx",
        "instance_id": "sphinx-doc__sphinx-8475",
    },
    # sphinx-doc__sphinx-8721: viewcode generates pages in epub builds
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In Sphinx's ext/viewcode.py, the collect_pages generator "
            "creates _modules/ pages for all builders including epub. "
            "EPUB readers cannot follow these local links. Fix: at the "
            "start of collect_pages(), add: if "
            "app.builder.name.startswith('epub') and not "
            "env.config.viewcode_enable_epub: return."
        ),
        "category": "sphinx_viewcode",
        "repo": "sphinx-doc/sphinx",
        "instance_id": "sphinx-doc__sphinx-8721",
    },
    # sympy__sympy-15017: rank-0 NDimArray has loop_size 0
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In SymPy's tensor/array/dense_ndim_array.py and "
            "sparse_ndim_array.py, the _loop_size for rank-0 (scalar) "
            "arrays is set to 0 when shape is empty. This makes "
            "len(array) == 0 and iteration yield nothing for scalars. "
            "A rank-0 array holds 1 element. Fix: change the fallback "
            "from 'if shape else 0' to 'if shape else 1' in all "
            "_new/__new__ methods across both dense and sparse classes."
        ),
        "category": "sympy_tensor",
        "repo": "sympy/sympy",
        "instance_id": "sympy__sympy-15017",
    },
    # sympy__sympy-19954: minimal_blocks deletes during iteration
    {
        "section": "REPO-SPECIFIC STRATEGIES",
        "prefix": "repo",
        "text": (
            "In SymPy's combinatorics/perm_groups.py, the "
            "_number_blocks / minimal_block method deletes items from "
            "num_blocks, blocks, and rep_blocks lists by index inside "
            "a for loop that iterates those same lists. This corrupts "
            "indices. Fix: collect indices to remove in a list, then "
            "delete in reverse order after the scan loop completes, "
            "using 'for i in reversed(to_remove): del num_blocks[i], "
            "blocks[i], rep_blocks[i]'."
        ),
        "category": "sympy_combinatorics",
        "repo": "sympy/sympy",
        "instance_id": "sympy__sympy-19954",
    },
]


def main():
    # Point to the repo-specific enhanced graph on port 7689
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7689")
    store = Neo4jStore(
        uri=uri,
        auth=(
            os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", "contextgraph123"),
        ),
    )
    embedder = get_embedding_client(
        "openai",
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_API_BASE"),
        model="text-embedding-3-large",
    )

    # Preflight check: ensure playbook_embedding vector index exists
    idx_result = store.driver.execute_query(
        "SHOW INDEXES YIELD name WHERE name = 'playbook_embedding' RETURN name"
    )
    if not idx_result.records:
        print(
            "ERROR: 'playbook_embedding' vector index not found. "
            "Run `scripts/ingest_playbook.py` first to create the index."
        )
        store.driver.close()
        sys.exit(1)

    # Get next ID for the repo prefix
    result = store.driver.execute_query(
        "MATCH (p:PlaybookEntry) RETURN p.prefix AS prefix, max(p.id) AS max_id"
    )
    max_ids = {}
    for r in result.records:
        prefix = r["prefix"]
        max_id = r["max_id"]
        if max_id and "-" in max_id:
            num = int(max_id.split("-")[1])
            max_ids[prefix] = num

    injected = 0
    skipped_dup = 0
    for s in STRATEGIES:
        prefix = s["prefix"]
        next_num = max_ids.get(prefix, 0) + 1
        entry_id = f"{prefix}-{next_num:05d}"
        max_ids[prefix] = next_num

        # Generate embedding
        embedding = embedder.embed(s["text"])

        # Check for duplicates (cosine similarity > 0.95)
        dup_result = store.driver.execute_query(
            """
            CALL db.index.vector.queryNodes('playbook_embedding', 3, $embedding)
            YIELD node, score
            WHERE score > 0.95
            RETURN node.id AS id, node.text AS text, score
            """,
            {"embedding": embedding},
        )
        if dup_result.records:
            existing = dup_result.records[0]
            print(
                f"SKIP (duplicate, score={existing['score']:.3f}): "
                f"{s['text'][:80]}..."
            )
            print(f"  Existing: {existing['text'][:80]}...")
            skipped_dup += 1
            continue

        # Insert as PlaybookEntry with repo metadata
        store.driver.execute_query(
            """
            CREATE (p:PlaybookEntry {
                id: $id,
                text: $text,
                section: $section,
                prefix: $prefix,
                category: $category,
                repo: $repo,
                instance_id: $instance_id,
                embedding: $embedding
            })
            """,
            {
                "id": entry_id,
                "text": s["text"],
                "section": s["section"],
                "prefix": s["prefix"],
                "category": s["category"],
                "repo": s["repo"],
                "instance_id": s["instance_id"],
                "embedding": embedding,
            },
        )
        print(
            f"INJECTED [{entry_id}] ({s['repo']}) "
            f"{s['instance_id']}: {s['text'][:60]}..."
        )
        injected += 1

    # Also link repo-specific strategies to relevant ErrorPattern nodes
    # where there's a textual match on the repo name
    link_result = store.driver.execute_query(
        """
        MATCH (p:PlaybookEntry)
        WHERE p.prefix = 'repo' AND p.repo IS NOT NULL
        WITH p, p.repo AS repo
        MATCH (e:ErrorPattern)
        WHERE e.error_keywords CONTAINS split(repo, '/')[1]
        MERGE (p)-[:ADDRESSES_ERROR]->(e)
        RETURN count(*) AS links_created
        """
    )
    links = (
        link_result.records[0]["links_created"] if link_result.records else 0
    )

    store.driver.close()
    print(f"\nDone: {injected} strategies injected, {skipped_dup} skipped (duplicates)")
    print(f"Error pattern links created: {links}")
    print(f"Total strategies defined: {len(STRATEGIES)}")


if __name__ == "__main__":
    main()
