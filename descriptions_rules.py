import inflect
import re

from collections import namedtuple, defaultdict
from typing import List, Dict, Tuple, Union, Set

GOSentence = namedtuple('GOSentence', ['prefix', 'terms', 'term_ids_dict', 'postfix', 'text', 'go_aspect',
                                       'evidence_group'])


class GOSentenceMerger(object):
    def __init__(self):
        self.postfix_list = []
        self.terms = set()
        self.terms_ids_dict = {}
        self.term_postfix_dict = {}
        self.evidence_groups = []
        self.term_evgroup_dict = {}


class GOSentencesCollection(object):
    """a group of GO sentences indexed by aspect"""
    def __init__(self, evidence_groups_list, go_prepostfix_sentences_map, merge_min_distance_from_root: int,
                 merge_num_terms_threshold: int, remove_parent_terms: bool):
        self.evidence_groups_list = evidence_groups_list
        self.go_prepostfix_sentences_map = go_prepostfix_sentences_map
        self.sentences_map = {}
        self.merge_min_distance_from_root = merge_min_distance_from_root
        self.merge_num_terms_threshold = merge_num_terms_threshold
        self.remove_parent_terms = remove_parent_terms

    def set_sentence(self, sentence: GOSentence) -> None:
        """add a sentence to the collection

        :param sentence: the sentence to add
        :type sentence: Sentence
        """
        if sentence is not None:
            self.sentences_map[(sentence.go_aspect, sentence.evidence_group)] = sentence

    def get_sentences(self, go_aspect: str, go_ontology, keep_only_best_group: bool = False,
                      merge_groups_with_same_prefix: bool = False) -> List[GOSentence]:
        """get all sentences containing the specified aspect

        :param go_aspect: a GO aspect
        :type go_aspect: str
        :param go_ontology: the go ontology object obtained from a data fetcher
        :param keep_only_best_group: whether to get only the evidence group with highest priority and discard
            the other evidence groups
        :type keep_only_best_group: bool
        :param merge_groups_with_same_prefix: whether to merge the phrases for evidence groups with the same prefix
        :type merge_groups_with_same_prefix: bool
        :return: the list of sentences containing the specified GO aspect
        :rtype: List[GOSentence]
        """
        sentences = []
        merged_sentences = defaultdict(GOSentenceMerger)
        for eg in self.evidence_groups_list:
            if (go_aspect, eg) in self.sentences_map:
                if merge_groups_with_same_prefix:
                    prefix = self.go_prepostfix_sentences_map[(go_aspect, eg)][0]
                    merged_sentences[prefix].postfix_list.append(self.go_prepostfix_sentences_map[(go_aspect, eg)][1])
                    merged_sentences[prefix].terms.update(self.sentences_map[(go_aspect, eg)].terms)
                    merged_sentences[prefix].terms_ids_dict.update(self.sentences_map[(go_aspect, eg)].term_ids_dict)
                    for term in self.sentences_map[(go_aspect, eg)].terms:
                        merged_sentences[prefix].term_postfix_dict[term] = self.go_prepostfix_sentences_map[
                            (go_aspect, eg)][1]
                    merged_sentences[prefix].evidence_groups.append(eg)
                    for term in self.sentences_map[(go_aspect, eg)].terms:
                        merged_sentences[prefix].term_evgroup_dict[term] = eg
                else:
                    sentences.append(self.sentences_map[(go_aspect, eg)])
                if keep_only_best_group:
                    break
        if merge_groups_with_same_prefix:
            for prefix, sent_merger in merged_sentences.items():
                # rem parents
                if self.remove_parent_terms:
                    term_ids_no_parents = get_term_ids_without_parents_from_terms_names(
                        go_terms_names=sent_merger.terms, term_ids_dict=sent_merger.terms_ids_dict,
                        go_ontology=go_ontology)
                    term_names_no_parents = set([go_ontology.query_term(term_id).name for term_id in
                                                 term_ids_no_parents])
                    sent_merger.terms = list(term_names_no_parents)
                    sent_merger.terms_ids_dict = {key: value for key, value in sent_merger.terms_ids_dict.items()
                                                  if key in set(term_names_no_parents)}
                # merge
                if self.merge_num_terms_threshold > 0:
                    merged_ids = get_merged_term_ids_by_common_ancestor_from_term_names(
                        go_terms_names=sent_merger.terms, term_ids_dict=sent_merger.terms_ids_dict,
                        go_ontology=go_ontology, min_distance_from_root=self.merge_min_distance_from_root,
                        min_number_of_terms=self.merge_num_terms_threshold)
                    sent_merger.terms = [go_ontology.query_term(term_id).name for term_id in merged_ids]
                    sent_merger.term_ids_dict = {go_ontology.query_term(term).name: go_ontology.query_term(term).id for
                                                 term in merged_ids}
            sentences = [GOSentence(prefix=prefix, terms=list(sent_merger.terms),
                                    term_ids_dict=sent_merger.terms_ids_dict,
                                    postfix=GOSentencesCollection.merge_postfix_phrases(sent_merger.postfix_list),
                                    text=compose_go_sentence(prefix=prefix,
                                                             go_term_names=list(sent_merger.terms),
                                                             postfix=GOSentencesCollection.merge_postfix_phrases(
                                                                 sent_merger.postfix_list)),
                                    go_aspect=go_aspect, evidence_group=", ".join(sent_merger.evidence_groups))
                         for prefix, sent_merger in merged_sentences.items() if len(sent_merger.terms) > 0]
        return sentences

    @staticmethod
    def merge_postfix_phrases(postfix_phrases: List[str]) -> str:
        """merge postfix phrases and remove possible redundant text at the beginning at at the end of the phrases

        :param postfix_phrases: the phrases to merge
        :type postfix_phrases: List[str]
        :return: the merged postfix phrase
        :rtype: str
        """
        if len(postfix_phrases) > 1:
            inf_engine = inflect.engine()
            shortest_phrase = sorted(zip(postfix_phrases, [len(phrase) for phrase in postfix_phrases]),
                                     key=lambda x: x[1])[0][0]
            first_part = ""
            for idx, letter in enumerate(shortest_phrase):
                if all(map(lambda x: x[idx] == shortest_phrase[idx], postfix_phrases)):
                    first_part += letter
                else:
                    break
            last_part = ""
            for idx, letter in zip(range(len(shortest_phrase)), reversed([l for l in shortest_phrase])):
                if all(map(lambda x: x[len(x) - idx - 1] == shortest_phrase[len(shortest_phrase) - idx - 1],
                           postfix_phrases)):
                    last_part = letter + last_part
                else:
                    break
            new_phrases = [phrase.replace(first_part, "").replace(last_part, "") for phrase in postfix_phrases]
            if len(last_part.strip().split(" ")) == 1:
                last_part = inf_engine.plural(last_part)
            if len(new_phrases) > 2:
                return first_part + ", ".join(new_phrases[0:-1]) + ", and " + new_phrases[-1] + last_part
            elif len(new_phrases) > 1:
                return first_part + " and ".join(new_phrases) + last_part
            else:
                return first_part + new_phrases[0] + last_part
        else:
            return postfix_phrases[0]


def generate_go_sentences(go_annotations: List[dict], go_ontology, evidence_groups_priority_list: List[str],
                          go_prepostfix_sentences_map: Dict[Tuple[str, str], Tuple[str, str]],
                          go_prepostfix_special_cases_sent_map: Dict[Tuple[str, str], Tuple[int, str, str, str]],
                          evidence_codes_groups_map: Dict[str, str], remove_parent_terms: bool = True,
                          merge_num_terms_threshold: int = 3,
                          merge_min_distance_from_root: int = 2) -> GOSentencesCollection:
    """generate GO sentences from a list of GO annotations

    :param go_annotations: the list of GO annotations for a given gene
    :type go_annotations: List[dict]
    :param go_ontology: the go ontology
    :param evidence_groups_priority_list: the list of evidence groups to consider, sorted by priority. Sentences of the
        first group (with highest priority) will be returned in first position and so on
    :type evidence_groups_priority_list: List[str]
    :param go_prepostfix_sentences_map: a map with prefix and postfix phrases, where keys are tuples of
        go_aspect, evidence_group and values are tuples prefix, postfix
    :type go_prepostfix_sentences_map: Dict[Tuple[str, str], Tuple[str, str]]
    :param go_prepostfix_special_cases_sent_map: a map for special prefix and postfix cases, where keys are tuples of
        go_aspect, evidence_group and values are tuples of id, match_regex, prefix, postfix. Match_regex is a regular
        expression that defines the match for the special case
    :type go_prepostfix_special_cases_sent_map: Dict[Tuple[str, str], Tuple[int, str, str, str]]
    :param evidence_codes_groups_map: a map between evidence codes and the groups they belong to
    :type evidence_codes_groups_map: Dict[str, str]
    :param remove_parent_terms: whether to remove parent terms from the list of terms in each sentence if at least
        one children term is present
    :type remove_parent_terms: bool
    :param merge_num_terms_threshold: whether to merge terms by common ancestor to
        reduce the number of terms in the set. The trimming algorithm will be applied only if the number of terms is
        greater than the specified number and the specified threshold is greater than 0
    :type merge_num_terms_threshold: int
    :param merge_min_distance_from_root: minimum distance from root terms for the selection of common ancestors
        during merging operations
    :type merge_min_distance_from_root: int
    :return: a collection of GO sentences
    :rtype: GOSentencesCollection
    """
    if len(go_annotations) > 0:
        go_terms_groups = defaultdict(set)
        for annotation in go_annotations:
            if annotation["Evidence"] in evidence_codes_groups_map:
                map_key = (annotation["Aspect"], evidence_codes_groups_map[annotation["Evidence"]])
                if map_key in go_prepostfix_special_cases_sent_map:
                    for special_case in go_prepostfix_special_cases_sent_map[map_key]:
                        if re.match(re.escape(special_case[1]), annotation["GO_Name"]):
                            map_key = (annotation["Aspect"], evidence_codes_groups_map[annotation["Evidence"]] +
                                       str(special_case[0]))
                            if evidence_codes_groups_map[annotation["Evidence"]] + str(special_case[0]) not in \
                                    evidence_groups_priority_list:
                                evidence_groups_priority_list.insert(evidence_groups_priority_list.index(
                                    evidence_codes_groups_map[annotation["Evidence"]]) + 1,
                                                                     evidence_codes_groups_map[annotation["Evidence"]] +
                                                                     str(special_case[0]))
                            break
                go_terms_groups[map_key].add((annotation["GO_Name"], annotation["GO_ID"]))
        sentences = GOSentencesCollection(evidence_groups_priority_list, go_prepostfix_sentences_map,
                                          merge_num_terms_threshold=merge_num_terms_threshold,
                                          merge_min_distance_from_root=merge_min_distance_from_root,
                                          remove_parent_terms=remove_parent_terms)
        for ((go_aspect, evidence_group), go_terms) in go_terms_groups.items():
            go_term_names = [term[0] for term in go_terms]
            term_ids_dict = {term_name: term_id for term_name, term_id in go_terms}
            if remove_parent_terms:
                term_ids_no_parents = get_term_ids_without_parents_from_terms_names(go_terms_names=go_term_names,
                                                                                    term_ids_dict=term_ids_dict,
                                                                                    go_ontology=go_ontology)
                go_term_names = [go_ontology.query_term(term_id).name for term_id in term_ids_no_parents]
                term_ids_dict = {go_ontology.query_term(term_id).name: go_ontology.query_term(term_id).id for term_id in
                                 term_ids_no_parents}
            if merge_num_terms_threshold > 0:
                merged_ids = get_merged_term_ids_by_common_ancestor_from_term_names(
                    go_terms_names=go_term_names, term_ids_dict=term_ids_dict, go_ontology=go_ontology,
                    min_distance_from_root=merge_min_distance_from_root, min_number_of_terms=merge_num_terms_threshold)
                go_term_names = [go_ontology.query_term(term_id).name for term_id in merged_ids]
                term_ids_dict = {go_ontology.query_term(term).name: go_ontology.query_term(term).id for term in
                                 merged_ids}
            sentences.set_sentence(_get_single_go_sentence(go_term_names=go_term_names,
                                                           go_term_ids_dict=term_ids_dict,
                                                           go_aspect=go_aspect,
                                                           evidence_group=evidence_group,
                                                           go_prepostfix_sentences_map=go_prepostfix_sentences_map))
        return sentences


def get_all_go_parent_names(go_id: str, go_ontology) -> List[str]:
    """get the name of all the ancestors of a GO term, excluding the root terms

    :param go_id: a valid GO id for the starting term
    :type go_id: str
    :param go_ontology: the go ontology
    :return: the list of ancestors of the term
    :rtype: List[str]
    """
    parent_names = []
    for parent in go_ontology.query_term(go_id).parents:
        # do not return root terms
        if len(go_ontology.query_term(parent.id).parents) > 0:
            parent_names.append(parent.name)
            parent_names.extend(get_all_go_parent_names(parent.id, go_ontology))
    return parent_names


def get_all_term_paths_to_root(go_id: str, go_ontology, min_distance_from_root: int = 0,
                               previous_path: Union[None, List[str]] = None) -> Set[Tuple[str]]:
    """get all possible paths connecting a go term to its root terms

    :param go_id: a valid GO id for the starting term
    :type go_id: str
    :param go_ontology: the go ontology
    :param min_distance_from_root: return only terms at a specified minimum distance from root terms
    :param previous_path: the path to get to the current node
    :type previous_path: Union[None, List[str]]
    :return: the set of paths connecting the specified term to its root terms, each of which contains a sequence of
        terms ids
    :rtype: Set[Tuple[str]]
    """
    if previous_path is None:
        previous_path = []
    new_path = previous_path[:]
    term_properties = go_ontology.query_term(go_id)
    if term_properties.depth >= min_distance_from_root:
        new_path.append(term_properties.id)
        parents = term_properties.parents
        if len(parents) > 0:
            # go up the tree, following a depth first visit
            paths_to_return = set()
            for parent in parents:
                for path in get_all_term_paths_to_root(go_id=parent.id, go_ontology=go_ontology,
                                                       previous_path=new_path,
                                                       min_distance_from_root=min_distance_from_root):
                    paths_to_return.add(path)
            return paths_to_return
    return {tuple(new_path)}


def get_term_ids_without_parents_from_terms_names(go_terms_names: List[str], term_ids_dict: Dict[str, str],
                                                  go_ontology) -> Set[str]:
    """remove parent terms (according to the provided go ontology) from a list of terms

    :param go_terms_names: the list of go terms from which the parents will be removed
    :type go_terms_names: List[str]
    :param term_ids_dict: a dictionary that maps term names into their GO ids
    :type term_ids_dict: Dict[str, str]
    :param go_ontology: the go ontology
    :return: the list of parents that have been removed from the list
    :rtype: Set[str]
    """
    go_terms_set = set(go_terms_names)
    for go_term_name in go_terms_names:
        for parent_name in get_all_go_parent_names(term_ids_dict[go_term_name], go_ontology):
            go_terms_set.discard(parent_name)
    return set([term_ids_dict[term] for term in go_terms_set])


def get_merged_term_ids_by_common_ancestor_from_term_names(go_terms_names: List[str], term_ids_dict: Dict[str, str],
                                                           go_ontology, min_distance_from_root: int = 3,
                                                           min_number_of_terms: int = 3) -> Set[str]:
    """remove terms with common ancestor and keep the ancestor term instead

    :param go_terms_names: the list of go terms from which the parents will be removed
    :type go_terms_names: List[str]
    :param term_ids_dict: a dictionary that maps term names into their GO ids
    :type term_ids_dict: Dict[str, str]
    :param min_distance_from_root: set a minimum distance from root terms for ancestors that can group children terms
    :type min_distance_from_root: int
    :param min_number_of_terms: minimum number of terms above which the merge operation is performed
    :type min_number_of_terms: int
    :param go_ontology: the go ontology
    :return: the set of merged terms
    :rtype: Set[str]
    """
    if len(go_terms_names) > min_number_of_terms:
        final_terms_set = set()
        ancestor_paths = defaultdict(list)
        term_paths = defaultdict(set)
        # step 1: get all path for each term and populate data structures
        for go_term_id in [term_ids_dict[term_name] for term_name in go_terms_names]:
            paths = get_all_term_paths_to_root(go_id=go_term_id, go_ontology=go_ontology,
                                               min_distance_from_root=min_distance_from_root)
            for path in paths:
                if len(path) > 1:
                    term_paths[go_term_id].add(path)
                    ancestor_paths[path[-1]].append(path)
        # step 2: merge terms and keep common ancestors
        for go_term_id in [term_ids_dict[term_name] for term_name in go_terms_names]:
            term_paths_copy = term_paths[go_term_id].copy()
            while len(term_paths_copy) > 0:
                curr_path = list(term_paths_copy.pop())
                selected_highest_ancestor = curr_path.pop()
                related_paths = ancestor_paths[selected_highest_ancestor]
                del ancestor_paths[selected_highest_ancestor]
                while len(curr_path) > 1:
                    curr_highest_ancestor = curr_path.pop()
                    if not all(map(lambda x: len(x) >= len(curr_path), related_paths)) or not \
                            all(map(lambda x: x == curr_path[-1], related_paths)):
                        break
                    selected_highest_ancestor = curr_highest_ancestor
                    if selected_highest_ancestor in ancestor_paths:
                        del ancestor_paths[selected_highest_ancestor]
                    for path in related_paths:
                        term_paths[path[0]].discard(path)
                final_terms_set.add(selected_highest_ancestor)
                for path in related_paths:
                    term_paths[path[0]].discard(path)
                if len(term_paths[go_term_id]) > 0:
                    term_paths_copy = term_paths[go_term_id].copy()
                else:
                    break
        return final_terms_set
    else:
        return set([term_ids_dict[term] for term in go_terms_names])


def compose_go_sentence(prefix: str, go_term_names: List[str], postfix: str) -> str:
    """compose the text of a sentence given its prefix, terms, and postfix

    :param prefix: the prefix of the sentence
    :type prefix: str
    :param go_term_names: a list of go terms
    :type go_term_names: List[str]
    :param postfix: the postfix of the sentence
    :type postfix: str
    :return: the text of the go sentence
    :rtype: str"""
    prefix = prefix + " "
    if postfix != "":
        postfix = " " + postfix
    if len(go_term_names) > 2:
        return prefix + ", ".join(go_term_names[0:-1]) + ", and " + go_term_names[len(go_term_names) - 1] + postfix
    elif len(go_term_names) > 1:
        return prefix + " and ".join(go_term_names) + postfix
    else:
        return prefix + go_term_names[0] + postfix


def _get_single_go_sentence(go_term_names: List[str], go_term_ids_dict: Dict[str, str], go_aspect: str,
                            evidence_group: str,
                            go_prepostfix_sentences_map: Dict[Tuple[str, str], Tuple[str, str]]) -> Union[GOSentence,
                                                                                                          None]:
    """build a go sentence

    :param go_term_names: list of go term names to be combined in the sentence
    :type go_term_names: List[str]
    :param go_term_ids_dict: map between term names and their ids
    :type go_term_ids_dict: Dict[str, str]
    :param go_aspect: go aspect
    :type go_aspect: str
    :param evidence_group: evidence group
    :type evidence_group: str
    :param go_prepostfix_sentences_map: map for prefix and postfix phrases
    :type go_prepostfix_sentences_map: Dict[Tuple[str, str], Tuple[str, str]]
    :return: the combined go sentence
    :rtype: Union[GOSentence, None]
    """
    if len(go_term_names) > 0:
        prefix = go_prepostfix_sentences_map[(go_aspect, evidence_group)][0]
        postfix = go_prepostfix_sentences_map[(go_aspect, evidence_group)][1]
        return GOSentence(prefix=prefix, terms=go_term_names, postfix=postfix,
                          term_ids_dict=go_term_ids_dict, text=compose_go_sentence(prefix, go_term_names, postfix),
                          go_aspect=go_aspect, evidence_group=evidence_group)
    else:
        return None
