import logging
import os
from collections import Counter
from datetime import datetime

import requests
from dotenv import load_dotenv
from matplotlib.image import thumbnail
from collections import Counter
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Any, Tuple
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from models.graph_builder import GraphBuilder
load_dotenv()


class SPARQLService:
    def __init__(self,service,options):
        self.fuseki_url = os.getenv("FUSEKI_URL")
        self.service = service
        self.options = options

    def get_recommendations(self, user_history: List[str], max_recommendations: int = 10) -> List[Dict[str, Any]]:
        """
        Generates personalized article recommendations based on user's reading history.
        Supports articles in any language.

        Args:
            user_history: List of article URLs from user's history
            max_recommendations: Maximum number of recommendations to return

        Returns:
            List of recommended articles with similarity scores
        """
        logging.info(f"Generating recommendations for user history: {user_history}")

        # Get viewed articles details
        viewed_articles = []
        for url in user_history:
            results = self.get_article_by_url(url)
            if results:
                viewed_articles.append(results)

        if not viewed_articles:
            return []

        # Extract user preferences
        preferences = self._extract_user_preferences(viewed_articles)

        # Get candidate articles using advanced search
        recommended_articles = self._get_candidate_articles(preferences)

        # Rank and filter recommendations
        final_recommendations = self._rank_articles(
            viewed_articles,
            recommended_articles,
            preferences,
            user_history,
            max_recommendations
        )

        return final_recommendations

    def _extract_user_preferences(self, viewed_articles: List[Dict]) -> Dict[str, Any]:
        """
        Extracts user preferences from viewing history.
        Language preference is not enforced to allow multilingual recommendations.
        """
        preferences = {
            'keywords': [],
            'wordcount_min': None,
            'wordcount_max': None,
            'author_name': None,
            'publisher': None,
            'datePublished_min': None,
            'datePublished_max': None
        }

        word_counts = []
        dates_published = []
        keywords = []
        authors = []
        publishers = []

        for article in viewed_articles:
            # Collect word counts
            if article.get('wordCount'):
                word_counts.append(article['wordCount'])

            # Collect publish dates
            if article.get('datePublished'):
                try:
                    date = datetime.fromisoformat(article['datePublished'].replace('Z', '+00:00'))
                    dates_published.append(date)
                except ValueError:
                    logging.error(f"Invalid date format: {article['datePublished']}")

            # Collect keywords (maintaining original language)
            if article.get('keywords'):
                if isinstance(article['keywords'], list):
                    keywords.extend(article['keywords'])
                elif isinstance(article['keywords'], str):
                    keywords.extend(article['keywords'].split())

            # Collect authors
            if article.get('author'):
                if isinstance(article['author'], list):
                    authors.extend([author['name'] for author in article['author'] if author.get('name')])
                elif isinstance(article['author'], dict):
                    if article['author'].get('name'):
                        authors.append(article['author']['name'])

            # Collect publishers
            if article.get('publisher'):
                if isinstance(article['publisher'], list):
                    publishers.extend([pub['name'] for pub in article['publisher'] if pub.get('name')])
                elif isinstance(article['publisher'], dict):
                    if article['publisher'].get('name'):
                        publishers.append(article['publisher']['name'])

        # Process collected data
        if word_counts:
            preferences['wordcount_min'] = int(np.percentile(word_counts, 25))
            preferences['wordcount_max'] = int(np.percentile(word_counts, 75))

        if dates_published:
            preferences['datePublished_min'] = min(dates_published)
            preferences['datePublished_max'] = max(dates_published)

        if keywords:
            # Get most common keywords (preserve original language)
            keyword_counter = Counter(keywords)
            preferences['keywords'] = ' '.join([k for k, _ in keyword_counter.most_common(10)])

        if authors:
            # Get most common author
            preferences['author_name'] = Counter(authors).most_common(1)[0][0]

        if publishers:
            # Get most common publisher
            preferences['publisher'] = Counter(publishers).most_common(1)[0][0]

        return preferences

    def _get_candidate_articles(self, preferences: Dict[str, Any]) -> List[Dict]:
        """
        Gets candidate articles using advanced search function.
        No language restriction applied.
        """
        # Try exact matches first
        articles, match_type = self.advanced_search(
            keywords=preferences['keywords'],
            wordcount_min=preferences['wordcount_min'],
            wordcount_max=preferences['wordcount_max'],
            author_name=preferences['author_name'],
            publisher=preferences['publisher'],
            datePublished_min=preferences['datePublished_min'],
            datePublished_max=preferences['datePublished_max'],
            inLanguage=None  # No language restriction
        )

        if not articles:
            # Try with relaxed criteria
            relaxed_preferences = preferences.copy()
            # Remove some constraints to get more results
            relaxed_preferences['author_name'] = None
            relaxed_preferences['publisher'] = None

            articles, match_type = self.advanced_search(
                keywords=relaxed_preferences['keywords'],
                wordcount_min=relaxed_preferences['wordcount_min'],
                wordcount_max=relaxed_preferences['wordcount_max'],
                datePublished_min=relaxed_preferences['datePublished_min'],
                datePublished_max=relaxed_preferences['datePublished_max'],
                inLanguage=None  # No language restriction
            )

        return articles

    def _rank_articles(
            self,
            viewed_articles: List[Dict],
            candidate_articles: List[Dict],
            preferences: Dict[str, Any],
            user_history: List[str],
            max_recommendations: int
    ) -> List[Dict]:
        """
        Ranks and filters candidate articles.
        Uses character-level TF-IDF for language-agnostic similarity.
        """
        if not candidate_articles:
            return []

        # Calculate content similarity using character-level n-grams
        vectorizer = TfidfVectorizer(
            analyzer='char',
            ngram_range=(3, 5),  # Use character trigrams to pentagrams
            stop_words=None  # Don't use stop words to keep language-agnostic
        )

        # Prepare text content
        viewed_texts = []
        candidate_texts = []

        for article in viewed_articles:
            text = []
            if article.get('headline'):
                text.append(article['headline'])
            if article.get('abstract'):
                text.append(article['abstract'])
            if article.get('keywords'):
                if isinstance(article['keywords'], list):
                    text.extend(article['keywords'])
                elif isinstance(article['keywords'], str):
                    text.extend(article['keywords'].split())
            viewed_texts.append(' '.join(text))

        for article in candidate_articles:
            text = []
            if article.get('headline'):
                text.append(article['headline'])
            if article.get('abstract'):
                text.append(article['abstract'])
            if article.get('keywords'):
                if isinstance(article['keywords'], list):
                    text.extend(article['keywords'])
                elif isinstance(article['keywords'], str):
                    text.extend(article['keywords'].split())
            candidate_texts.append(' '.join(text))

        try:
            # Calculate TF-IDF and similarity
            tfidf_matrix = vectorizer.fit_transform(viewed_texts + candidate_texts)
            similarity_matrix = cosine_similarity(tfidf_matrix)

            # Calculate average similarity to viewed articles
            num_viewed = len(viewed_texts)
            similarity_scores = similarity_matrix[num_viewed:, :num_viewed].mean(axis=1)

            # Combine with metadata similarity
            scored_articles = []
            for idx, article in enumerate(candidate_articles):
                if article.get('url') not in user_history:
                    article_copy = article.copy()
                    article_copy['similarity_score'] = float(similarity_scores[idx])

                    # Add metadata matching score
                    metadata_score = self._calculate_metadata_similarity(article, preferences)

                    # Combine scores (70% content, 30% metadata)
                    article_copy['final_score'] = (
                            0.7 * article_copy['similarity_score'] +
                            0.3 * metadata_score
                    )

                    scored_articles.append(article_copy)

            # Sort by final score
            ranked_articles = sorted(
                scored_articles,
                key=lambda x: x.get('final_score', 0),
                reverse=True
            )

            return ranked_articles[:max_recommendations]

        except Exception as e:
            logging.error(f"Error in ranking articles: {e}")
            return []

    def _calculate_metadata_similarity(self, article: Dict, preferences: Dict) -> float:
        """
        Calculates similarity score based on metadata matching.
        Language-agnostic implementation.
        """
        score = 0.0

        # Author match
        if article.get('author') and preferences.get('author_name'):
            if isinstance(article['author'], list):
                if any(author.get('name') == preferences['author_name'] for author in article['author']):
                    score += 0.3
            elif isinstance(article['author'], dict):
                if article['author'].get('name') == preferences['author_name']:
                    score += 0.3

        # Publisher match
        if article.get('publisher') and preferences.get('publisher'):
            if isinstance(article['publisher'], list):
                if any(pub.get('name') == preferences['publisher'] for pub in article['publisher']):
                    score += 0.3
            elif isinstance(article['publisher'], dict):
                if article['publisher'].get('name') == preferences['publisher']:
                    score += 0.3

        # Word count range match
        if (article.get('wordCount') and
                preferences.get('wordcount_min') and
                preferences.get('wordcount_max')):
            if (preferences['wordcount_min'] <= article['wordCount'] <= preferences['wordcount_max']):
                score += 0.4

        return score

    def create_graph(self, url):
        """
        Creates an RDF graph using GraphBuilder.
        Args:
            url: URL of the article to create the graph for.
        Returns:
            RDF graph in Turtle and JSON-LD formats.
        """
        logging.info(f"Creating RDF graph for URL: {url}")
        graph_builder = GraphBuilder(url, self.service, self.options)
        key_article = ['articleBody', 'articleSection', 'wordCount', 'abstract', 'audio', 'author', 'editor',
                       'publisher', 'image','@type',
                                    'dateCreated', 'datePublished', 'dateModified', 'headline', 'inLanguage',
                       'keywords', 'thumbnailUrl', 'thumbnail']

        if graph_builder.json_ld_data is not None:
            graph_builder.insert_json_ld_to_graph(url, graph_builder.json_ld_data, key_article)
        if graph_builder.rdfa_data is not None:
            graph_builder.insert_rdfa_to_graph(url, graph_builder.rdfa_data)
        graph_builder.add_articleBody_to_graph(url)
        graph_builder.add_content_length_to_graph(url)
        graph_builder.add_inLanguage_to_graph(url)
        graph_builder.add_keywords_to_graph(url)
        rdf_graph = graph_builder.graph
        graph_turtle = rdf_graph.serialize(format="turtle")
        graph_json = rdf_graph.serialize(format="json-ld")
        return graph_turtle, graph_json

    def insert_graph(self, graph_data):
        """
        Inserts an RDF graph into the Fuseki dataset.
        Args:
            graph_data: RDF graph data in Turtle format.
        Returns:
            Response from the Fuseki server.
        """
        logging.info("Inserting RDF graph into Fuseki dataset")
        sparql_endpoint = f"{self.fuseki_url}/NEPR-2024/data"
        headers = {'Content-Type': 'text/turtle'}

        try:
            response = requests.post(sparql_endpoint, data=graph_data, headers=headers)
            if response.status_code == 200:
                print("RDF Graph uploaded successfully!")
            else:
                print(f"Failed to upload RDF Graph. Status code: {response.status_code}")
                print("Error message:", response.text)
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to SPARQL endpoint: {e}")

    def create_and_insert_graph(self, url):
        """
        Builds an RDF graph for the given URL using GraphBuilder and inserts it into the Fuseki dataset.
        Args:
            url: URL of the article to create the graph for.
        Returns:
            Success flag, message, and RDF graph data in JSON-LD format.
        """
        logging.info(f"Creating and inserting RDF graph for URL: {url}")
        try:
            graph_builder = GraphBuilder(url, self.service, self.options)
            key_article = ['articleBody', 'articleSection', 'wordCount', 'abstract', 'audio', 'author', 'editor',
                           'publisher', 'image','@type',
                                        'dateCreated', 'datePublished', 'dateModified', 'headline', 'inLanguage',
                           'keywords', 'thumbnailUrl', 'thumbnail']

            if graph_builder.json_ld_data is not None:
                graph_builder.insert_json_ld_to_graph(url, graph_builder.json_ld_data, key_article)
            logging.info("Rdfa data", graph_builder.rdfa_data)
            if graph_builder.rdfa_data:
                graph_builder.insert_rdfa_to_graph(url, graph_builder.rdfa_data)

            graph_builder.add_keywords_to_graph(url)
            graph_builder.add_content_length_to_graph(url)
            graph_builder.add_articleBody_to_graph(url)
            graph_builder.add_inLanguage_to_graph(url)

            turtle_data = graph_builder.graph.serialize(format="turtle")
            if turtle_data is None:
                return False, "Graph creation failed", None
            if turtle_data == "\n":
                return False, "Graph creation failed", None
            response = requests.post(
                f"{self.fuseki_url}/NEPR-2024/data",
                data=turtle_data,
                headers={'Content-Type': 'text/turtle'},
                timeout=10
            )

            if response.status_code != 200:
                return False, f"Upload failed: {response.text}", None
            results = self.get_article_by_url(url)
            if results:
                return True, "Graph created successfully", results
            return False, "Graph created successfully, but article not found", None
        except Exception as e:
            return False, f"Error: {str(e)}", None

    def search_articles_by_keywords(self, keywords):
        """
        Searches for articles based on keywords.
        Args:
            keywords: Keywords to search for.
        Returns:
            List of articles matching the keywords.
        """
        logging.info(f"Searching for articles with keywords: {keywords}")
        keywords_list = keywords.split()

        exact_articles = self.search_exact_match(keywords_list)
        if exact_articles:
            return exact_articles, "Exact matches"

        partial_articles = self.search_partial_match(keywords_list)
        return partial_articles, "Partial matches"

    def advanced_search(self, keywords=None, wordcount=None, inLanguage=None, author_name=None, author_nationality=None, publisher=None, datePublished=None, wordcount_min=None, wordcount_max=None, datePublished_min=None, datePublished_max=None):
        """
        Searches for articles based on advanced search criteria.
        Args:
            keywords: Keywords to search for.
            wordcount: Number of words in the article.
            inLanguage: Language of the article.
            author_name: Name of the author.
            author_nationality: Nationality of the author.
            publisher: Name of the publisher.
            datePublished: Date the article was published.
        Returns:
            List of articles matching the search criteria.
        """
        logging.info(f"Advanced search with keywords: {keywords}, wordcount: {wordcount}, inLanguage: {inLanguage}, author_name: {author_name}, author_nationality: {author_nationality}, publisher: {publisher}, datePublished: {datePublished}")

        exact_matches = self.search_advanced_exact_match(keywords, wordcount, inLanguage, author_name,
                                                         author_nationality, publisher, datePublished, wordcount_min,
                                                         wordcount_max, datePublished_min, datePublished_max)
        if exact_matches:
            return exact_matches, "Exact matches"

        partial_matches = self.search_advanced_partial_match(keywords, wordcount, inLanguage, author_name,
                                                             author_nationality, publisher, datePublished,
                                                             wordcount_min, wordcount_max, datePublished_min,
                                                             datePublished_max)
        return partial_matches, "Partial matches"

    def get_all_articles(self):
        logging.info("Retrieving all articles from the Fuseki dataset")
        results = self.search_all_articles()
        return results

    def search_exact_match(self, keywords_list):
        """
        Searches for articles that match all keywords with additional metadata.
        Args:
            keywords_list: List of keywords to search for.
        Returns:
            List of articles with detailed metadata matching all keywords.
        """
        logging.info(f"Searching for articles with exact match keywords: {keywords_list}")
        exact_match_query = f"""
            PREFIX schema: <http://schema.org/>
            SELECT DISTINCT ?article ?headline ?abstract ?author ?publisher ?datePublished ?thumbnailUrl
            WHERE {{
                ?article schema:headline ?headline .
                OPTIONAL {{ ?article schema:abstract ?abstract }}
                OPTIONAL {{ 
                    ?article schema:author ?authorObj .
                    ?authorObj schema:name ?author 
                }}
                OPTIONAL {{ 
                    ?article schema:publisher ?publisherObj .
                    ?publisherObj schema:name ?publisher 
                }}
                OPTIONAL {{ ?article schema:datePublished ?datePublished }}
                OPTIONAL {{ ?article schema:thumbnailUrl ?thumbnailUrl }}
                FILTER(
                    {" && ".join([
            f'EXISTS {{ ?article schema:keywords ?keyword{index} . '
            f'FILTER(CONTAINS(LCASE(STR(?keyword{index})), "{keyword.lower()}")) }}'
            for index, keyword in enumerate(keywords_list)
        ])}
                )
            }}
            LIMIT 10
        """
        logging.info(exact_match_query)

        # Execute the query and process results
        raw_results = self.execute_search_sparql_query(exact_match_query)

        # Transform raw results into a more structured format
        processed_results = []
        print(raw_results)
        for result in raw_results:
            processed_result = {
                'url': result.get('url', ''),
                'headline': result.get('headline', ''),
                'abstract': result.get('abstract', ''),
                'author': result.get('author', ''),
                'datePublished': result.get('datePublished', ''),
                'thumbnailUrl': result.get('thumbnailUrl', ''),
                'keywords': keywords_list  # Include the original search keywords
            }
            processed_results.append(processed_result)

        return processed_results

    def search_advanced_exact_match(self, keywords=None, wordcount=None, inLanguage=None, author_name=None,
                                    author_nationality=None, publisher=None, datePublished=None, wordcount_min=None,
                                    wordcount_max=None, datePublished_min=None, datePublished_max=None):
        """
        Searches for articles that match all keywords with additional metadata.
        Args:
            keywords: Keywords to search for.
            wordcount: Number of words in the article.
            inLanguage: Language of the article.
            author_name: Name of the author.
            author_nationality: Nationality of the author.
            publisher: Name of the publisher.
            datePublished: Date the article was published.
        Returns:
            List of articles with detailed metadata matching all search criteria.
        """
        logging.info(
            f"Advanced search with keywords: {keywords}, wordcount: {wordcount}, inLanguage: {inLanguage}, author_name: {author_name}, author_nationality: {author_nationality}, publisher: {publisher}, datePublished: {datePublished}")
        filters = []
        keywords_list = []
        if keywords:
            keywords_list = keywords.split()
            filters = [
                f'EXISTS {{ ?article schema:keywords ?keyword{index} . FILTER(CONTAINS(LCASE(STR(?keyword{index})), "{keyword.lower()}")) }}'
                for index, keyword in enumerate(keywords_list)
            ]

        if wordcount:
            filters.append(
                f'EXISTS {{ ?article <http://schema.org/wordCount> "{wordcount}"^^<http://www.w3.org/2001/XMLSchema#integer> }}')

        if inLanguage:
            filters.append(f'EXISTS {{ ?article schema:inLanguage "{inLanguage}" }}')

        if author_name:
            filters.append(f'EXISTS {{ ?authorObj schema:name "{author_name}" }}')

        if author_nationality:
            filters.append(f'EXISTS {{ ?authorObj schema:nationality "{author_nationality}" }}')

        if publisher:
            filters.append(f'EXISTS {{ ?publisherObj schema:name "{publisher}" }}')

        if datePublished:
            if isinstance(datePublished, str):
                datePublished = datetime.fromisoformat(datePublished)
            datePublished = datePublished.isoformat(timespec='seconds')+ "+00:00"
            filters.append(
                f'EXISTS {{ ?article schema:datePublished "{datePublished}"^^<http://www.w3.org/2001/XMLSchema#dateTime> }}')

        if wordcount_min and wordcount_max:
            filters.append(
                f'EXISTS {{ ?article schema:wordCount ?wordCount . FILTER(?wordCount >= {wordcount_min} && ?wordCount <= {wordcount_max}) }}')

        if datePublished_min and datePublished_max:
            if isinstance(datePublished_min, str):
                datePublished_min = datetime.fromisoformat(datePublished_min)
            if isinstance(datePublished_max, str):
                datePublished_max = datetime.fromisoformat(datePublished_max)
            datePublished_min = datePublished_min.isoformat(timespec='seconds')+ "+00:00"
            datePublished_max = datePublished_max.isoformat(timespec='seconds') + "+00:00"
            filters.append(
                f'EXISTS {{ ?article schema:datePublished ?datePublished . FILTER(?datePublished >= "{datePublished_min}"^^<http://www.w3.org/2001/XMLSchema#dateTime> && ?datePublished <= "{datePublished_max}"^^<http://www.w3.org/2001/XMLSchema#dateTime>) }}')

        exact_match_query = f"""
            PREFIX schema: <http://schema.org/>
            SELECT DISTINCT ?article ?headline ?abstract ?author ?publisher ?datePublished ?thumbnailUrl
            WHERE {{
                ?article schema:headline ?headline .
                OPTIONAL {{ ?article schema:abstract ?abstract }}
                OPTIONAL {{
                    ?article schema:author ?authorObj .
                    ?authorObj schema:name ?author
                }}
                OPTIONAL {{
                    ?article schema:publisher ?publisherObj .
                    ?publisherObj schema:name ?publisher
                }}
                OPTIONAL {{ ?article schema:datePublished ?datePublished }}
                OPTIONAL {{ ?article schema:thumbnailUrl ?thumbnailUrl }}
                FILTER(
                    {" && ".join(filters)}
                )
            }}
            LIMIT 10
        """
        logging.info(exact_match_query)

        # Execute the query and process results
        raw_results = self.execute_search_sparql_query(exact_match_query)

        # Transform raw results into a more structured format
        processed_results = []
        for result in raw_results:
            processed_result = {
                'url': result.get('url', ''),
                'headline': result.get('headline', ''),
                'abstract': result.get('abstract', ''),
                'author': result.get('author', ''),
                'datePublished': result.get('datePublished', ''),
                'thumbnailUrl': result.get('thumbnailUrl', ''),
                'keywords': keywords_list  # Include the original search keywords
            }
            processed_results.append(processed_result)

        return processed_results


    def search_partial_match(self, keywords_list):
        """
        Searches for articles that match at least one keyword.
        Args:
            keywords_list: List of keywords to search for.
        Returns:
            List of articles matching at least one keyword.
        """
        logging.info(f"Searching for articles with partial match keywords: {keywords_list}")
        partial_match_query = f"""
            PREFIX schema: <http://schema.org/>
            SELECT DISTINCT ?article ?headline ?abstract ?author ?publisher ?datePublished ?thumbnailUrl
            WHERE {{
                ?article schema:headline ?headline .
                OPTIONAL {{ ?article schema:abstract ?abstract }}
                OPTIONAL {{ 
                    ?article schema:author ?authorObj .
                    ?authorObj schema:name ?author 
                }}
                OPTIONAL {{ 
                    ?article schema:publisher ?publisherObj .
                    ?publisherObj schema:name ?publisher 
                }}
                OPTIONAL {{ ?article schema:datePublished ?datePublished }}
                OPTIONAL {{ ?article schema:thumbnailUrl ?thumbnailUrl }}
                FILTER(
                    {" || ".join([
            f'EXISTS {{ ?article schema:keywords ?keyword{index} . '
            f'FILTER(CONTAINS(LCASE(STR(?keyword{index})), "{keyword.lower()}")) }}'
            for index, keyword in enumerate(keywords_list)
        ])}
                )
            }}
            LIMIT 10
        """
        return self.execute_search_sparql_query(partial_match_query)

    def search_advanced_partial_match(self, keywords, wordcount=None, inLanguage=None, author_name=None,
                                      author_nationality=None, publisher=None, datePublished=None,
                                      wordcount_min=None, wordcount_max=None, datePublished_min=None,
                                      datePublished_max=None):
        """
        Searches for articles that match at least one keyword with additional metadata.
        Args:
            keywords: Keywords to search for.
            wordcount: Number of words in the article.
            inLanguage: Language of the article.
            author_name: Name of the author.
            author_nationality: Nationality of the author.
            publisher: Name of the publisher.
            datePublished: Date the article was published.
        Returns:
            List of articles with detailed metadata matching at least one search criteria.
        """
        logging.info(
            f"Advanced partial search with keywords: {keywords}, wordcount: {wordcount}, inLanguage: {inLanguage}, author_name: {author_name}, author_nationality: {author_nationality}, publisher: {publisher}, datePublished: {datePublished}")
        keywords_list = []
        filters = []
        if keywords:
            keywords_list = keywords.split()
            filters = [
                f'EXISTS {{ ?article schema:keywords ?keyword{index} . FILTER(CONTAINS(LCASE(STR(?keyword{index})), "{keyword.lower()}")) }}'
                for index, keyword in enumerate(keywords_list)
            ]

        if wordcount:
            filters.append(
                f'EXISTS {{ ?article <http://schema.org/wordCount> "{wordcount}"^^<http://www.w3.org/2001/XMLSchema#integer> }}')

        if inLanguage:
            filters.append(f'EXISTS {{ ?article schema:inLanguage "{inLanguage}" }}')

        if author_name:
            filters.append(f'EXISTS {{ ?authorObj schema:name "{author_name}" }}')

        if author_nationality:
            filters.append(f'EXISTS {{ ?authorObj schema:nationality "{author_nationality}" }}')

        if publisher:
            filters.append(f'EXISTS {{ ?publisherObj schema:name "{publisher}" }}')

        if datePublished:
            if isinstance(datePublished, str):
                datePublished = datetime.fromisoformat(datePublished)
            datePublished = datePublished.isoformat(timespec='seconds') + "+00:00"
            filters.append(
                f'EXISTS {{ ?article schema:datePublished "{datePublished}"^^<http://www.w3.org/2001/XMLSchema#dateTime> }}')

        if wordcount_min and wordcount_max:
            filters.append(
                f'EXISTS {{ ?article schema:wordCount ?wordCount . FILTER(?wordCount >= {wordcount_min} && ?wordCount <= {wordcount_max}) }}')

        if datePublished_min and datePublished_max:
            if isinstance(datePublished_min, str):
                datePublished_min = datetime.fromisoformat(datePublished_min)
            if isinstance(datePublished_max, str):
                datePublished_max = datetime.fromisoformat(datePublished_max)
            datePublished_min = datePublished_min.isoformat(timespec='seconds') + "+00:00"
            datePublished_max = datePublished_max.isoformat(timespec='seconds') + "+00:00"
            filters.append(
                f'EXISTS {{ ?article schema:datePublished ?datePublished . FILTER(?datePublished >= "{datePublished_min}"^^<http://www.w3.org/2001/XMLSchema#dateTime> && ?datePublished <= "{datePublished_max}"^^<http://www.w3.org/2001/XMLSchema#dateTime>) }}')

        partial_match_query = f"""
            PREFIX schema: <http://schema.org/>
            SELECT DISTINCT ?article ?headline ?abstract ?author ?publisher ?datePublished ?thumbnailUrl
            WHERE {{
                ?article schema:headline ?headline .
                OPTIONAL {{ ?article schema:abstract ?abstract }}
                OPTIONAL {{
                    ?article schema:author ?authorObj .
                    ?authorObj schema:name ?author
                }}
                OPTIONAL {{
                    ?article schema:publisher ?publisherObj .
                    ?publisherObj schema:name ?publisher
                }}
                OPTIONAL {{ ?article schema:datePublished ?datePublished }}
                OPTIONAL {{ ?article schema:thumbnailUrl ?thumbnailUrl }}
                FILTER(
                    {" || ".join(filters)}
                )
            }}
            LIMIT 10
        """
        logging.info(partial_match_query)

        # Execute the query and process results
        raw_results = self.execute_search_sparql_query(partial_match_query)

        # Transform raw results into a more structured format
        processed_results = []
        for result in raw_results:
            processed_result = {
                'url': result.get('url', ''),
                'headline': result.get('headline', ''),
                'abstract': result.get('abstract', ''),
                'author': result.get('author', ''),
                'datePublished': result.get('datePublished', ''),
                'thumbnailUrl': result.get('thumbnailUrl', ''),
                'keywords': keywords_list  # Include the original search keywords
            }
            processed_results.append(processed_result)

        return processed_results

    def search_all_articles(self):
        search_query = f"""
            PREFIX schema: <http://schema.org/>
            SELECT DISTINCT ?article ?headline ?abstract ?author ?publisher ?datePublished ?thumbnailUrl
            WHERE {{
                ?article schema:headline ?headline .
                OPTIONAL {{ ?article schema:abstract ?abstract }}
                OPTIONAL {{ 
                    ?article schema:author ?authorObj .
                    ?authorObj schema:name ?author 
                }}
                OPTIONAL {{ 
                    ?article schema:publisher ?publisherObj .
                    ?publisherObj schema:name ?publisher 
                }}
                OPTIONAL {{ ?article schema:datePublished ?datePublished }}
                OPTIONAL {{ ?article schema:thumbnailUrl ?thumbnailUrl }}
            }}
            LIMIT 10
        """
        logging.info(search_query)
        return self.execute_search_sparql_query(search_query)


    def search_certain_articles(self, links):
        """
        Searches for articles that match the given URLs.
        Args:
            links: List of URLs to search for.
        Returns:
            List of articles matching the URLs.
        """
        logging.info(f"Searching for articles with URLs: {links}")
        articles = []
        for link in links:
            article = self.get_article_by_url(link)
            if article:
                articles.append(article)
        return articles

    def execute_search_sparql_query(self, query):
        """
        Executes a SPARQL query and returns the results.
        Args:
            query: SPARQL query to execute.
        Returns:
            List of articles from the query results.
        """
        logging.info(f"Executing SPARQL query: {query}")
        sparql_endpoint = f"{self.fuseki_url}/NEPR-2024/query"
        headers = {'Content-Type': 'application/sparql-query'}
        response = requests.post(sparql_endpoint, data=query, headers=headers)
        articles = []
        if response.status_code == 200:
            results = response.json()
            for result in results['results']['bindings']:
                article = {}
                if 'headline' in result:
                    article['headline'] = result['headline']['value']
                if 'article' in result:
                    article['url'] = result['article']['value']
                if 'abstract' in result:
                    article['abstract'] = result['abstract']['value']
                if 'author' in result:
                    article['author'] = result['author']['value']
                if 'datePublished' in result:
                    article['datePublished'] = result['datePublished']['value']
                if 'thumbnailUrl' in result:
                    article['thumbnailUrl'] = result['thumbnailUrl']['value']
                if 'publisher' in result:
                    article['publisher'] = result['publisher']['value']
                articles.append(article)
        return articles

    def execute_sparql_query(self, query):
        """
        Executes a SPARQL query and returns the results.
        Args:
            query: SPARQL query to execute.
        Returns:
            List of results from the query.
        """
        sparql_endpoint = f"{self.fuseki_url}/NEPR-2024/query"
        headers = {'Content-Type': 'application/sparql-query'}
        try:
            response = requests.post(sparql_endpoint, data=query, headers=headers)
            response.raise_for_status()
            return response.json().get('results', {}).get('bindings', [])
        except requests.exceptions.RequestException as e:
            logging.error(f"Error executing SPARQL query: {e}")
            return []

    @staticmethod
    def populate_person(result, results):
        person_data = {
            "name": None,
            "@type": None,
            "jobTitle": None,
            "address": None,
            "affiliation": None,
            "birthDate": None,
            "birthPlace": None,
            "deathDate": None,
            "deathPlace": None,
            "email": None,
            "familyName": None,
            "gender": None,
            "givenName": None,
            "nationality": None
        }
        subject = result.get('o', {}).get('value')
        sub_predicates = [result.get('subP', {}).get('value') for result in results if
                          result.get('o', {}).get('value') == subject]
        sub_objects = [result.get('subO', {}).get('value') for result in results if
                       result.get('o', {}).get('value') == subject]
        for sub_predicate, sub_object_value in zip(sub_predicates, sub_objects):
            if sub_predicate == "http://schema.org/name":
                person_data["name"] = sub_object_value
            elif sub_predicate == "http://schema.org/@type":
                person_data["@type"] = sub_object_value
            elif sub_predicate == "http://schema.org/jobTitle":
                person_data["jobTitle"] = sub_object_value
            elif sub_predicate == "http://schema.org/address":
                person_data["address"] = sub_object_value
            elif sub_predicate == "http://schema.org/affiliation":
                person_data["affiliation"] = sub_object_value
            elif sub_predicate == "http://schema.org/birthDate":
                person_data["birthDate"] = sub_object_value
            elif sub_predicate == "http://schema.org/birthPlace":
                person_data["birthPlace"] = sub_object_value
            elif sub_predicate == "http://schema.org/deathDate":
                person_data["deathDate"] = sub_object_value
            elif sub_predicate == "http://schema.org/deathPlace":
                person_data["deathPlace"] = sub_object_value
            elif sub_predicate == "http://schema.org/email":
                person_data["email"] = sub_object_value
            elif sub_predicate == "http://schema.org/familyName":
                person_data["familyName"] = sub_object_value
            elif sub_predicate == "http://schema.org/gender":
                person_data["gender"] = sub_object_value
            elif sub_predicate == "http://schema.org/givenName":
                person_data["givenName"] = sub_object_value
            elif sub_predicate == "http://schema.org/nationality":
                person_data["nationality"] = sub_object_value
        return person_data

    @staticmethod
    def populate_image_data(result, results):
        image_data = {
            "height": None,
            "width": None,
            "url": None,
            "@type": None
        }
        subject = result.get('o', {}).get('value')
        #get the sub-predicate and sub-object from results that gave o = subject
        sub_predicates = [result.get('subP', {}).get('value') for result in results if result.get('o', {}).get('value') == subject]
        sub_objects = [result.get('subO', {}).get('value') for result in results if result.get('o', {}).get('value') == subject]
        for sub_predicate, sub_object_value in zip(sub_predicates, sub_objects):
            if sub_predicate == "http://schema.org/height":
                image_data["height"] = int(sub_object_value)
            elif sub_predicate == "http://schema.org/width":
                image_data["width"] = int(sub_object_value)
            elif sub_predicate == "http://schema.org/@type":
                image_data["@type"] = sub_object_value
            elif sub_predicate == "http://schema.org/url":
                image_data["url"] = sub_object_value
        return image_data

    @staticmethod
    def populate_organization(result, results):
        organization_data = {
            "name": None,
            "@type": None,
            "address": None,
            "affiliation": None,
            "email": None
        }
        subject = result.get('o', {}).get('value')
        logging.info(f"Subject: {subject}")
        # get the sub-predicate and sub-object from results that gave o = subject
        sub_predicates = [result.get('subP', {}).get('value') for result in results if
                          result.get('o', {}).get('value') == subject]
        sub_objects = [result.get('subO', {}).get('value') for result in results if
                       result.get('o', {}).get('value') == subject]
        for sub_predicate, sub_object_value in zip(sub_predicates, sub_objects):
            logging.info(f"Sub-predicate: {sub_predicate}, Sub-object: {sub_object_value}")
            if sub_predicate == "http://schema.org/name":
                organization_data["name"] = sub_object_value
            elif sub_predicate == "http://schema.org/@type":
                organization_data["@type"] = sub_object_value
            elif sub_predicate == "http://schema.org/address":
                organization_data["address"] = sub_object_value
            elif sub_predicate == "http://schema.org/affiliation":
                organization_data["affiliation"] = sub_object_value
            elif sub_predicate == "http://schema.org/email":
                organization_data["email"] = sub_object_value
        return organization_data

    @staticmethod
    def populate_audio_data(result, results):
        audio_data = {
            "caption": None,
            "transcript": None,
            "@type": None,
            "contentUrl": None,
            "duration": None,
            "embedUrl": None,
            "height": None,
            "uploadDate": None,
            "width": None
        }
        subject = result.get('o', {}).get('value')
        sub_predicates = [result.get('subP', {}).get('value') for result in results if
                          result.get('o', {}).get('value') == subject]
        sub_objects = [result.get('subO', {}).get('value') for result in results if
                       result.get('o', {}).get('value') == subject]
        for sub_predicate, sub_object_value in zip(sub_predicates, sub_objects):
            if sub_predicate == "http://schema.org/caption":
                audio_data["caption"] = sub_object_value
            elif sub_predicate == "http://schema.org/transcript":
                audio_data["transcript"] = sub_object_value
            elif sub_predicate == "http://schema.org/@type":
                audio_data["@type"] = sub_object_value
            elif sub_predicate == "http://schema.org/contentUrl":
                audio_data["contentUrl"] = sub_object_value
            elif sub_predicate == "http://schema.org/duration":
                audio_data["duration"] = sub_object_value
            elif sub_predicate == "http://schema.org/embedUrl":
                audio_data["embedUrl"] = sub_object_value
            elif sub_predicate == "http://schema.org/height":
                audio_data["height"] = int(sub_object_value)
            elif sub_predicate == "http://schema.org/uploadDate":
                audio_data["uploadDate"] = sub_object_value
            elif sub_predicate == "http://schema.org/width":
                audio_data["width"] = int(sub_object_value)
        return audio_data

    @staticmethod
    def populate_video_data(result, results):
        video_data = {
            "caption": None,
            "transcript": None,
            "@type": None,
            "contentUrl": None,
            "duration": None,
            "embedUrl": None,
            "height": None,
            "uploadDate": None,
            "width": None
        }
        subject = result.get('o', {}).get('value')
        sub_predicates = [result.get('subP', {}).get('value') for result in results if
                          result.get('o', {}).get('value') == subject]
        sub_objects = [result.get('subO', {}).get('value') for result in results if
                       result.get('o', {}).get('value') == subject]
        for sub_predicate, sub_object_value in zip(sub_predicates, sub_objects):
            if sub_predicate == "http://schema.org/caption":
                video_data["caption"] = sub_object_value
            elif sub_predicate == "http://schema.org/transcript":
                video_data["transcript"] = sub_object_value
            elif sub_predicate == "http://schema.org/@type":
                video_data["@type"] = sub_object_value
            elif sub_predicate == "http://schema.org/contentUrl":
                video_data["contentUrl"] = sub_object_value
            elif sub_predicate == "http://schema.org/duration":
                video_data["duration"] = sub_object_value
            elif sub_predicate == "http://schema.org/embedUrl":
                video_data["embedUrl"] = sub_object_value
            elif sub_predicate == "http://schema.org/height":
                video_data["height"] = int(sub_object_value)
            elif sub_predicate == "http://schema.org/uploadDate":
                video_data["uploadDate"] = sub_object_value
            elif sub_predicate == "http://schema.org/width":
                video_data["width"] = int(sub_object_value)
        return video_data

    def populate_article_data(self, results, url):
        article_data = {
            "@context": "http://schema.org",
            "@type": None,
            "url": url,
            "author": [],
            "publisher": [],
            "image": [],
            "keywords": [],
            "datePublished": None,
            "headline": None,
            "articleBody": None,
            "wordCount": None,
            "inLanguage": None,
            "thumbnailUrl": None,
            "thumbnail": [],
            "articleSection": None,
            "abstract": None,
            "audio": [],
            "video": [],
            "editor": {},
            "dateCreated": None,
            "dateModified": None,
        }
        # Process query results and populate article_data
        for result in results:
            try:
                logging.info(f"Processing result: {result}")
                predicate = result['p']['value']
                object_value = result['o']['value']
                if 'subP' in result:
                    sub_predicate = result['subP']['value']
                else:
                    sub_predicate = None
                if 'subO' in result:
                    sub_object = result['subO']['value']
                else:
                    sub_object = None
                logging.info(f"Predicate: {predicate}, Object Value: {object_value}")

                # Map predicates to JSON structure
                if predicate == "http://schema.org/@type":
                    article_data["@type"] = object_value
                elif predicate == "http://schema.org/headline":
                    article_data["headline"] = object_value
                elif predicate == "http://schema.org/datePublished":
                    article_data["datePublished"] = object_value
                elif predicate == "http://schema.org/dateModified":
                    article_data["dateModified"] = object_value
                elif predicate == "http://schema.org/articleBody":
                    article_data["articleBody"] = object_value
                elif predicate == "http://schema.org/wordCount":
                    article_data["wordCount"] = int(object_value)
                elif predicate == "http://schema.org/inLanguage":
                    article_data["inLanguage"] = object_value
                elif predicate == "http://schema.org/thumbnailUrl":
                    article_data["thumbnailUrl"] = object_value
                elif predicate == "http://schema.org/articleSection":
                    article_data["articleSection"] = object_value
                elif predicate == "http://schema.org/abstract":
                    article_data["abstract"] = object_value
                elif predicate == "http://schema.org/keywords":
                    logging.info(f"Appending keyword: {object_value}")
                    article_data["keywords"].append(object_value)
                elif predicate == "http://schema.org/url":
                    article_data["url"] = object_value
                elif predicate == "http://schema.org/author":
                    if "author" not in article_data:
                        article_data["author"] = []
                    author_data = {}
                    if sub_predicate == "http://schema.org/@type":
                        if sub_object == "Person":
                            author_data = self.populate_person(result, results)
                        elif sub_object == "Organization":
                            author_data = self.populate_organization(result, results)
                        if author_data not in article_data["author"]:
                            article_data["author"].append(author_data)
                elif predicate == "http://schema.org/publisher":
                    if "publisher" not in article_data:
                        article_data["publisher"] = []
                    publisher_data = {}
                    if sub_predicate == "http://schema.org/@type":
                        if sub_object == "Person":
                            publisher_data = self.populate_person(result, results)
                        elif sub_object == "Organization":
                            publisher_data = self.populate_organization(result, results)
                        if publisher_data not in article_data["publisher"] and publisher_data:
                            article_data["publisher"].append(publisher_data)
                elif predicate == "http://schema.org/image":
                    if "image" not in article_data:
                        article_data["image"] = []
                    image_data = self.populate_image_data(result, results)
                    if image_data not in article_data["image"]:
                        logging.info(f"Image data: {image_data}")
                        article_data["image"].append(image_data)
                elif predicate == "http://schema.org/thumbnail":
                    if "thumbnail" not in article_data:
                        article_data["thumbnail"] = {}
                    thumbnail_data = self.populate_image_data(result, results)
                    if thumbnail_data:
                        article_data["thumbnail"] = thumbnail_data
                elif predicate == "http://schema.org/audio":
                    if "audio" not in article_data:
                        article_data["audio"] = []
                    audio_data = self.populate_audio_data(results, result)
                    if audio_data:
                        article_data["audio"].append(audio_data)

                elif predicate == "http://schema.org/video":
                    if "video" not in article_data:
                        article_data["video"] = []
                    video_data = self.populate_video_data(results, result)
                    if video_data:
                        article_data["video"].append(video_data)

                elif predicate == "http://schema.org/editor":
                    if "editor" not in article_data:
                        article_data["editor"] = []
                    editor_data = self.populate_person(result, results)
                    if editor_data not in article_data["editor"]:
                        article_data["editor"].append(editor_data)
                elif predicate == "http://schema.org/dateCreated":
                    article_data["dateCreated"] = object_value
            except Exception as e:
                logging.error(f"Error processing result: {e}")
        return article_data

    def get_article_by_url(self, url):
        """
        Retrieves an article from the Fuseki dataset by its URL.

        Args:
            url: URL of the article to retrieve.
        Returns:
            Article data in structured JSON format.
        """
        try:
            # SPARQL query with dynamic URL
            query = f"""
            PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
            SELECT ?p ?o ?subP ?subO
            WHERE {{
              <{url}> ?p ?o .

              OPTIONAL {{
                FILTER (isIRI(?o))
                ?o ?subP ?subO
              }}
            }}
            """

            # Execute query (you'll need to implement the actual SPARQL execution)
            article_data = {}
            results = self.execute_sparql_query(query)
            try:
                article_data = self.populate_article_data(results, url)
            except Exception as e:
                logging.error(f"Error processing result: {e}")
            return article_data

        except Exception as e:
            logging.error(f"Error retrieving article by URL: {e}")
            return None

    def delete_article_by_url(self, url):
        """
            Deletes an article from the Fuseki dataset by its URL.
            Args:
                url: URL of the article to delete.
            Returns:
                Success message or error message.
            """
        logging.info(f"Deleting article with URL: {url}")
        try:
            query = f"""
                PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
                PREFIX schema: <http://schema.org/>

                DELETE {{
                  <{url}> ?p ?o .
                  ?o ?subP ?subO
                }}
                WHERE {{
                  <{url}> ?p ?o .

                  # Prevent deletion of triples where the predicate is author, publisher, or editor
                  FILTER NOT EXISTS {{
                    <{url}> ?p ?o .
                    FILTER (?p IN (schema:author, schema:publisher, schema:editor))
                  }}

                  OPTIONAL {{
                    FILTER (isIRI(?o))
                    ?o ?subP ?subO
                    # Prevent deletion of linked triples related to author, publisher, or editor
                    FILTER NOT EXISTS {{
                      ?o ?subP ?subO .
                      FILTER (?subP IN (schema:author, schema:publisher, schema:editor))
                    }}
                  }}
                }};

                DELETE {{
                  <{url}> ?p ?o .
                }}
                WHERE {{
                  <{url}> ?p ?o .
                }}
                """
            logging.info(query)
            response = requests.post(
                f"{self.fuseki_url}/NEPR-2024/update",
                data=query,
                headers={'Content-Type': 'application/sparql-update'},
                timeout=10
            )
            logging.info(response)
            if response.status_code == 204:
                return True, "Article deleted successfully"
            else:
                return False, f"Failed to delete article: {response.text}"
        except Exception as e:
            logging.error(f"Error deleting article by URL: {e}")
            return False, f"Error: {str(e)}"

    def get_all_data(self):
        """
        Retrieves all data from the Fuseki dataset.
        Returns:
            All data from the dataset.
        """
        try:
            query = """
            SELECT ?s ?p ?o
            WHERE {
              ?s ?p ?o
            }
            """
            results = self.execute_sparql_query(query)
            return results
        except Exception as e:
            logging.error(f"Error retrieving all data: {e}")
            return None
