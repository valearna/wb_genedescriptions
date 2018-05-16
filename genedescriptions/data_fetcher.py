import gzip
import json
import tarfile
import urllib.request
import shutil
import os
import logging
import re
from enum import Enum
from itertools import chain
from abc import ABCMeta, abstractmethod
from collections import namedtuple, defaultdict
from typing import List, Iterable, Dict
from genedescriptions.descriptions_rules import SingleDescStats

Gene = namedtuple('Gene', ['id', 'name', 'dead', 'pseudo'])

class AnnotationType(Enum):
    GO = 1
    DO = 2

def get_parents(self):
    """Return parent GO IDs."""
    relationship = getattr(self, "relationship", defaultdict(set))
    if relationship and "part_of" in relationship:
        return set([parent for parent in chain(self.parents, relationship["part_of"])])
    else:
        return self.parents


class OntoTerm(object):
    """go term with the same properties and methods defined by goatools GOTerm

    only the properties used for gene descriptions are included
    """

    def __init__(self, name: str, depth: int, node_id: str, parents: List, children: List, is_obsolete: bool, ontology):
        self.depth = depth
        self.name = name
        self.id = node_id
        self._parents = parents
        self._children = children
        self._ontology = ontology
        self.is_obsolete = is_obsolete

    def get_parents(self) -> List["OntoTerm"]:
        """get the parent terms of the current term

        :return: the list of parents of the term
        :rtype: List[OntoTerm]
        """
        return [self._ontology.query_term(parent_id) for parent_id in self._parents]

    def get_children(self) -> List["OntoTerm"]:
        """get the child terms of the current term

        :return: the list of children of the term
        :rtype: List[OntoTerm]
        """
        return [self._ontology.query_term(child_id) for child_id in self._children]


class Ontology(metaclass=ABCMeta):
    """ontology interface with properties and methods needed for gene descriptions

    the structure of this interface mirrors that of goatools GODag class for compatibility with goatools package
    """

    @abstractmethod
    def __init__(self):
        pass

    @abstractmethod
    def query_term(self, term_id: str) -> OntoTerm:
        """retrieve a term from its ID

        :param term_id: the ID of the term
        :type term_id: str
        :return: the term
        :rtype: OntoTerm
        """
        pass


class DataFetcher(metaclass=ABCMeta):
    """retrieve data for gene descriptions from different sources"""

    @abstractmethod
    def __init__(self, go_terms_exclusion_list: List[str], go_terms_replacement_dict: Dict[str, str]):
        self.go_data = defaultdict(list)
        self.go_ontology = None
        self.go_terms_exclusion_list = go_terms_exclusion_list
        self.go_terms_replacement_dict = go_terms_replacement_dict
        self.do_ontology = None
        self.do_data = defaultdict(list)

    @abstractmethod
    def load_gene_data(self):
        pass

    @abstractmethod
    def get_gene_data(self) -> Gene:
        pass

    @abstractmethod
    def load_go_data(self) -> None:
        pass

    @abstractmethod
    def load_disease_data(self) -> None:
        pass

    def load_all_data(self) -> None:
        """retrieve all data needed to generate the descriptions and load it into the data structures of the data
        fetcher
        """
        self.load_gene_data()
        self.load_go_data()
        self.load_disease_data()

    def get_annotations(self, geneid: str, annot_type: AnnotationType = AnnotationType.GO,
                        include_obsolete: bool = False, include_negative_results: bool = False,
                        priority_list: Iterable = ("EXP", "IDA", "IPI", "IMP", "IGI", "IEP", "IC", "ISS", "ISO", "ISA",
                                                   "ISM", "IGC", "IBA", "IBD", "IKR", "IRD", "RCA", "IEA"),
                        desc_stats: SingleDescStats = None) -> List[dict]:
        """
        retrieve go annotations for a given gene id and for a given aspect. The annotations are unique for each pair
        <gene_id, go_term_id>. This means that when multiple annotations for the same pair are found in the go data, the
        one with the evidence code with highest priority is returned (see the *priority_list* parameter to set the
        priority according to evidence codes)

        :param geneid: the id of the gene related to the annotations to retrieve, in standard format
        :type geneid: str
        :param annot_type: type of annotations to read
        :type annot_type: AnnotationType
        :param include_obsolete: whether to include obsolete annotations
        :type include_obsolete: bool
        :param include_negative_results: whether to include negative results
        :type include_negative_results: bool
        :param priority_list: the priority list for the evidence codes. If multiple annotations with the same go_term
            are found, only the one with highest priority is returned. The first element in the list has the highest
            priority, whereas the last has the lowest. Only annotations with evidence codes in the priority list are
            returned. All other annotations are ignored
        :type priority_list: List[str]
        :param desc_stats: an object containing the description statistics where to save the total number of annotations
            for the gene
        :type desc_stats: SingleDescStats
        :return: the list of go annotations for the given gene
        :rtype: List[GOAnnotation]
        """
        dataset = None
        if annot_type == AnnotationType.GO:
            dataset = self.go_data
        elif annot_type == AnnotationType.DO:
            dataset = self.do_data
        priority_map = dict(zip(priority_list, reversed(range(len(list(priority_list))))))
        annotations = [annotation for annotation in dataset[geneid] if (include_obsolete or
                       not annotation["Is_Obsolete"]) and (include_negative_results or "NOT" not in
                                                           annotation["Qualifier"])]
        if desc_stats:
            desc_stats.total_num_go_annotations = len(annotations)
        id_selected_annotation = {}
        for annotation in annotations:
            if annotation["Evidence"] in priority_map.keys():
                if annotation["GO_ID"] in id_selected_annotation:
                    if priority_map[annotation["Evidence"]] > \
                            priority_map[id_selected_annotation[annotation["GO_ID"]]["Evidence"]]:
                        id_selected_annotation[annotation["GO_ID"]] = annotation
                else:
                    id_selected_annotation[annotation["GO_ID"]] = annotation
        if desc_stats:
            desc_stats.num_prioritized_go_annotations = len(id_selected_annotation.keys())
        return [annotation for annotation in id_selected_annotation.values()]

    def get_go_ontology(self):
        return self.go_ontology

    def get_do_ontology(self):
        return self.do_ontology


class RawDataFetcher(DataFetcher):
    """retrieve data for gene descriptions from raw data files"""

    @abstractmethod
    def __init__(self, go_terms_exclusion_list: List[str], go_terms_replacement_dict: Dict[str, str],
                 cache_location: str, use_cache: bool = False):
        super().__init__(go_terms_exclusion_list=go_terms_exclusion_list,
                         go_terms_replacement_dict=go_terms_replacement_dict)
        self.chebi_file_url = ""
        self.chebi_file_cache_path = ""
        self.ls_ontology = None
        self.an_ontology = None
        self.gene_data = {}
        self.use_cache = use_cache
        self.gene_data_cache_path = ""
        self.gene_data_url = ""
        self.go_ontology_cache_path = ""
        self.go_ontology_url = ""
        self.go_annotations_cache_path = ""
        self.go_annotations_url = ""
        self.go_id_name = "DB_Object_ID"
        self.do_ontology_cache_path = ""
        self.do_ontology_url = ""
        self.do_annotations_cache_path = ""
        self.do_annotations_url = ""

    @staticmethod
    def _get_cached_file(cache_path: str, file_source_url):
        if not os.path.isfile(cache_path):
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            urllib.request.urlretrieve(file_source_url, cache_path)
        file_path = cache_path
        if cache_path.endswith(".gz"):
            with gzip.open(cache_path, 'rb') as f_in, open(cache_path.replace(".gz", ""), 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            file_path = cache_path.replace(".gz", "")
        return file_path

    def get_gene_data(self, include_dead_genes: bool = False, include_pseudo_genes: bool = False) -> Gene:
        """get all gene data from the fetcher, returning one gene per call

        :param include_dead_genes: whether to include dead genes in the results
        :type include_dead_genes: bool
        :param include_pseudo_genes: whether to include pseudo genes in the results
        :type include_dead_genes: bool
        :return: data for one gene per each call, including gene_id and gene_name
        :rtype: Gene
        """
        if len(self.gene_data) == 0:
            self.load_gene_data()
        for gene_id, gene_obj in self.gene_data.items():
            if (include_dead_genes or not gene_obj.dead) and (include_pseudo_genes or not gene_obj.pseudo):
                yield gene_obj

    def load_go_data(self) -> None:
        """read go data and gene ontology. After calling this function, go annotations containing mapped go names can
        be retrieved by using the :meth:`data_fetcher.WBRawDataFetcher.get_go_annotations` function
        """
        import goatools
        from goatools.obo_parser import GODag
        from Bio.UniProt.GOA import gafiterator
        goatools.obo_parser.GOTerm.get_parents = get_parents
        self.go_ontology = GODag(self._get_cached_file(file_source_url=self.go_ontology_url,
                                                       cache_path=self.go_ontology_cache_path),
                                 optional_attrs=["relationship"])
        file_path = self._get_cached_file(cache_path=self.go_annotations_cache_path,
                                          file_source_url=self.go_annotations_url)
        lines_to_skip = 0
        with open(file_path) as file:
            while True:
                if file.readline().strip().startswith("!gaf-version:"):
                    break
                lines_to_skip += 1
        with open(file_path) as file:
            for _ in range(lines_to_skip):
                next(file)
            for annotation in gafiterator(file):
                if self.go_ontology.query_term(annotation["GO_ID"]) and \
                        self.go_ontology.query_term(annotation["GO_ID"]).id not in self.go_terms_exclusion_list:
                    mapped_annotation = annotation
                    mapped_annotation["GO_Name"] = self.go_ontology.query_term(mapped_annotation["GO_ID"]).name
                    mapped_annotation["GO_ID"] = self.go_ontology.query_term(mapped_annotation["GO_ID"]).id
                    for regex_to_substitute, regex_target in self.go_terms_replacement_dict.items():
                        mapped_annotation["GO_Name"] = re.sub(regex_to_substitute, regex_target,
                                                              mapped_annotation["GO_Name"])
                    mapped_annotation["Is_Obsolete"] = \
                        self.go_ontology.query_term(mapped_annotation["GO_ID"]).is_obsolete
                    self.go_data[annotation[self.go_id_name]].append(mapped_annotation)


class WBRawDataFetcher(RawDataFetcher):
    """data fetcher for WormBase raw files for a single species"""

    def __init__(self, go_terms_exclusion_list: List[str], go_terms_replacement_dict: Dict[str, str],
                 raw_files_source: str, cache_location: str, release_version: str, species: str, project_id: str,
                 use_cache: bool = False):
        """create a new data fetcher

        :param go_terms_exclusion_list: list of go ids for terms to exclude
        :type go_terms_exclusion_list: List[str]
        :param go_terms_replacement_dict: dictionary to map go terms to be renamed. Term names can be regex
        :type go_terms_replacement_dict: Dict[str, str]
        :param raw_files_source: base url where to fetch the raw files
        :type raw_files_source: str
        :param cache_location: path to cache directory
        :type cache_location: str
        :param release_version: WormBase release version for the input files
        :type release_version: str
        :param species: WormBase species to fetch
        :type species: str
        :param project_id: project id associated with the species
        :type project_id: str
        :param use_cache: whether to use cached files. If cache is empty, files are downloading from source and stored
            in cache
        :type use_cache: bool
        """
        super().__init__(go_terms_exclusion_list=go_terms_exclusion_list,
                         go_terms_replacement_dict=go_terms_replacement_dict, use_cache=use_cache,
                         cache_location=cache_location)
        self.gene_data_cache_path = os.path.join(cache_location, "wormbase", release_version, "species", species,
                                                 project_id, "annotation", species + '.' + project_id +
                                                 '.' + release_version + ".geneIDs.txt.gz")
        self.gene_data_url = raw_files_source + '/' + release_version + '/species/' + species + '/' + project_id + \
                             '/annotation/' + species + '.' + project_id + '.' + release_version + '.geneIDs.txt.gz'
        self.go_ontology_cache_path = os.path.join(cache_location, "wormbase", release_version, "ONTOLOGY",
                                                   "gene_ontology." + release_version + ".obo")
        self.go_ontology_url = raw_files_source + '/' + release_version + '/ONTOLOGY/gene_ontology.' + \
                               release_version + '.obo'
        self.go_annotations_cache_path = os.path.join(cache_location, "wormbase", release_version, "species", species,
                                                      project_id, "annotation", species + '.' + project_id + '.' +
                                                      release_version + ".go_annotations.gaf.gz")
        self.go_annotations_url = raw_files_source + '/' + release_version + '/species/' + species + '/' + \
                                  project_id + '/annotation/' + species + '.' + project_id + '.' + release_version + \
                                  '.go_annotations.gaf.gz'
        self.do_ontology_url = raw_files_source + '/' + release_version + '/ONTOLOGY/disease_ontology.' + \
                               release_version + '.obo'
        self.do_ontology_cache_path = os.path.join(cache_location, "wormbase", release_version, "ONTOLOGY",
                                                   "disease_ontology." + release_version + ".obo")
        self.do_annotations_cache_path = os.path.join(cache_location, "wormbase", release_version, "species", species,
                                                      project_id, "annotation", species + '.' + project_id + '.' +
                                                      release_version + ".do_annotations.wb")
        self.do_annotations_url = raw_files_source + '/' + release_version + '/ONTOLOGY/disease_association.' + \
                                  release_version + '.wb'
        self.do_annotations_new_cache_path = os.path.join(cache_location, "wormbase", release_version, "species",
                                                          species, project_id, "annotation", species + '.' +
                                                          project_id + '.' + release_version +
                                                          ".do_annotations.daf.txt")
        self.do_annotations_new_url = raw_files_source + '/' + release_version + '/ONTOLOGY/disease_association.' + \
                                      release_version + '.daf.txt'

    def load_gene_data(self) -> None:
        """load all gene data"""
        if len(self.gene_data.items()) == 0:
            file_path = self._get_cached_file(cache_path=self.gene_data_cache_path, file_source_url=self.gene_data_url)
            with open(file_path) as file:
                for line in file:
                    fields = line.strip().split(',')
                    name = fields[2] if fields[2] != '' else fields[3]
                    self.gene_data[fields[1]] = Gene(fields[1], name, fields[4] == "Dead", False)

    def load_disease_data(self) -> None:
        from Bio.UniProt.GOA import gafiterator
        from goatools.obo_parser import GODag
        self.do_ontology = GODag(self._get_cached_file(file_source_url=self.do_ontology_url,
                                                       cache_path=self.do_ontology_cache_path))
        file_path = self._get_cached_file(cache_path=self.do_annotations_cache_path,
                                          file_source_url=self.do_annotations_url)
        lines_to_skip = 0
        with open(file_path) as file:
            while True:
                if file.readline().strip().startswith("!gaf-version:"):
                    break
                lines_to_skip += 1
        with open(file_path) as file:
            for _ in range(lines_to_skip):
                next(file)
            for annotation in gafiterator(file):
                if self.do_ontology.query_term(annotation["GO_ID"]) and annotation["Evidence"] == "IEA":
                    mapped_annotation = annotation
                    mapped_annotation["GO_Name"] = self.do_ontology.query_term(mapped_annotation["GO_ID"]).name
                    mapped_annotation["GO_ID"] = self.do_ontology.query_term(mapped_annotation["GO_ID"]).id
                    mapped_annotation["Is_Obsolete"] = \
                        self.do_ontology.query_term(mapped_annotation["GO_ID"]).is_obsolete
                    self.do_data[annotation[self.go_id_name]].append(mapped_annotation)

        file_path = self._get_cached_file(cache_path=self.do_annotations_new_cache_path,
                                          file_source_url=self.do_annotations_new_url)
        header = True
        for line in open(file_path):
            if not line.strip().startswith("!"):
                if not header:
                    linearr = line.strip().split("\t")
                    if self.do_ontology.query_term(linearr[10]) and linearr[16] != "IEA":
                        mapped_annotation = {"Evidence": linearr[16],
                                             "GO_Name": self.do_ontology.query_term(linearr[10]).name,
                                             "GO_ID": self.do_ontology.query_term(linearr[10]).id,
                                             "Is_Obsolete": self.do_ontology.query_term(linearr[10]).is_obsolete,
                                             "DB_Object_ID": linearr[2],
                                             "DB_Object_Symbol": linearr[3],
                                             "Qualifier": linearr[9],
                                             "Aspect": "D"}
                        self.do_data[linearr[2][3:]].append(mapped_annotation)
                else:
                    header = False


class AGRRawDataFetcher(RawDataFetcher):
    """data fetcher for AGR raw files for a single species"""

    def __init__(self, go_terms_exclusion_list: List[str], go_terms_replacement_dict: Dict[str, str],
                 raw_files_source: str, cache_location: str, release_version: str, main_file_name: str,
                 bgi_file_name: str, go_annotations_file_name: str, organism_name: str, use_cache: bool = False):
        """create a new data fetcher

        :param go_terms_exclusion_list: list of go ids for terms to exclude
        :type go_terms_exclusion_list: List[str]
        :param go_terms_replacement_dict: dictionary to map go terms to be renamed. Term names can be regex
        :type go_terms_replacement_dict: Dict[str, str]
        :param raw_files_source: base url where to fetch the raw files
        :type raw_files_source: str
        :param cache_location: path to cache directory
        :type cache_location: str
        :param release_version: WormBase release version for the input files
        :type release_version: str
        :param main_file_name: file name of the main tar.gz file containing gene information
        :type main_file_name: str
        :param bgi_file_name: file name of the bgi file containing gene information
        :type bgi_file_name: str
        :param go_annotations_file_name: file name of the obo file containing go annotations
        :type go_annotations_file_name: str
        :param organism_name: name of the organism
        :type organism_name: str
        :param use_cache: whether to use cached files. If cache is empty, files are downloading from source and stored
            in cache
        :type use_cache: bool
        """
        super().__init__(go_terms_exclusion_list=go_terms_exclusion_list,
                         go_terms_replacement_dict=go_terms_replacement_dict, use_cache=use_cache,
                         cache_location=cache_location)
        self.main_data_cache_path = os.path.join(cache_location, "agr", release_version, "main", main_file_name)
        self.main_data_url = raw_files_source + '/' + main_file_name
        self.bgi_file_name = bgi_file_name
        self.go_ontology_cache_path = os.path.join(cache_location, "agr", release_version, "GO", "go.obo")
        self.go_ontology_url = raw_files_source + '/' + release_version + '/GO/' + 'go.obo'
        self.go_annotations_cache_path = os.path.join(cache_location, "agr", release_version, "GO", "ANNOT",
                                                      go_annotations_file_name)
        self.go_annotations_url = raw_files_source + '/' + release_version + '/GO/ANNOT/' + go_annotations_file_name
        self.go_id_name = "DB_Object_Symbol"

    def load_gene_data(self) -> None:
        if len(self.gene_data.items()) == 0:
            if not os.path.isfile(self.main_data_cache_path):
                os.makedirs(os.path.dirname(self.main_data_cache_path), exist_ok=True)
                urllib.request.urlretrieve(self.main_data_url, self.main_data_cache_path)
            if not os.path.isfile(os.path.join(os.path.dirname(self.main_data_cache_path), self.bgi_file_name)):
                tar = tarfile.open(self.main_data_cache_path)
                tar.extractall(path=os.path.dirname(self.main_data_cache_path))
            with open(os.path.join(os.path.dirname(self.main_data_cache_path), self.bgi_file_name)) as fileopen:
                bgi_content = json.load(fileopen)
                for gene in bgi_content["data"]:
                    self.gene_data[gene["symbol"]] = Gene(gene["symbol"], gene["symbol"], False, False)

    def load_disease_data(self) -> None:
        pass
