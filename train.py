"""
Neural topic modeling - doc2topic
Samuel Rönnqvist, TurkuNLP <saanro@utu.fi>
"""

# Config GPU memory usage
import tensorflow as tf
from keras.backend.tensorflow_backend import set_session, clear_session
config = tf.ConfigProto()
config.gpu_options.per_process_gpu_memory_fraction = -1
config.gpu_options.allow_growth = True
set_session(tf.Session(config=config))

#from keras.preprocessing.sequence import skipgrams
from model import *
from measures import *
import sys
import collections
import numpy as np
import matplotlib.pyplot as plt
import random
import csv, json
from os.path import isfile
import heapq
from sklearn.metrics.pairwise import cosine_similarity

from keras.callbacks import ModelCheckpoint


### Hyperparameters
n_topics   = 200 # Increase with number of documents
l1_doc     = 0.000002#15
l1_word    = 0.000000015
lr         = 0.015
batch_size = 10*1024 #~6-8/10k for 10-100k documents, too high might slow down learning
ns_rate    = 1 # Negative sampling rate, 1-2 recommended
n_epochs   = 20 # Max
min_count  = 10 # Minimum word count

stopwords_fi_lemma = set("ja tai ei se että olla joka jos mikä mitä tämä kun eli ne hän siis jos#ei mutta kuin".split())


def read_data(filename):
	""" Read data from a single file, return data and vocabulary
		Format: one document per line, tokens are space separated """
	data = []
	vocab = set()
	cntr = collections.defaultdict(lambda: 0)
	print("Reading documents...", end='', flush=True)
	f = open(filename)

	while True:
		line = f.readline()
		if not line:
			break
		data.append(line.strip().lower().split())
		#vocab |= set(data[-1])
		for token in data[-1]:
			cntr[token] += 1
		if len(data) % 100 == 0:
			print("\rReading documents: %d" % len(data), end='', flush=True)

	print()
	return data, cntr#vocab


def get_topic_words(wordvecs, top_n=10, stopwords=set()):
	stopidxs = set([token2idx[word] for word in stopwords])
	topic_words = {}
	for topic in range(wordvecs.shape[1]):
		topic_words[topic] = heapq.nlargest(top_n+len(stopwords), enumerate(L1normalize(self.wordvecs[:,topic])), key=lambda x:x[1])
		topic_words[topic] = [(s,i) for i,s in topic_words[topic] if i not in stopidxs]
	return topic_words


def most_similar_words(wordvecs, word, n=20):
	idx = token2idx[word]
	sims = heapq.nlargest(n, enumerate(cosine_similarity(wordvecs[idx:idx+1,:], wordvecs)[0]), key=lambda x:x[1])
	return [(idx2token[i], s) for i, s in sims]


def write_log(log, filename="log.csv"):
	file_exists = isfile(filename)
	with open(filename, 'a') as csvfile:
		writer = csv.DictWriter(csvfile, sorted(log.keys()))
		if not file_exists:
			writer.writeheader()
		writer.writerow(log)


L2 = (lambda x: np.linalg.norm(x, 2))
L1 = (lambda x: np.linalg.norm(x, 1))
L1normalize = (lambda x: x/L1(x))
cosine = (lambda a,b: np.dot(a, b)/(L2(a)*L2(b)) if sum(a) != 0 and sum(b) != 0 else 0)
relufy = np.vectorize(lambda x: max(0., x))


#### Main begin
if len(sys.argv) < 2:
	print("Usage: %s <documents file>" % sys.argv[0])
	sys.exit()

### Prepare data
data, cntr = read_data(sys.argv[1])
vocab_len = len([cnt for cnt in cntr.values() if cnt > min_count])
n_docs = len(data)
print("Vocabulary size: %d" % vocab_len)

#cntr = collections.defaultdict(lambda: 0)
#print("Counting words...")
#cntr, cocntr = count_words(data, save_to="stt_lemmas.json")

print("Loading word count data...")
cntr, cocntr = load_counts("stt_lemma_counts_100k.json")

input_docs, input_tokens, outputs = [], [], []
token2idx = collections.defaultdict(lambda: len(token2idx))
for doc_id, tokens in enumerate(data):
	if doc_id % 100 == 0:
		print("\rPreparing data: %d%%" % ((doc_id+1)/len(data)*100+1), end='', flush=True)
	# Filter tokens by frequency and map them to IDs (creates mapping table on the fly)
	token_ids = [token2idx[token] for token in tokens if cntr[token] > min_count]
	for i, idx in enumerate(token_ids):
		input_tokens.append(idx)
		input_tokens += [random.randint(1, vocab_len-1) for x in range(ns_rate)]
		input_docs += [doc_id]*(ns_rate+1)
		outputs += [1]+[0]*ns_rate
	"""
	# Alt: use keras skipgrams
	pairs, labels = skipgrams([-1]+token_ids+[-1], len(vocab)+1, window_size=1, negative_samples=ns_rate)
	input_tokens += [context_token for _, context_token in pairs if context_token != -1]
	input_docs += [doc_id for _, context_token in pairs if context_token != -1]
	outputs += [labels[i] for i in range(len(pairs)) if pairs[i][1] != -1]"""

print()
del data
input_docs = np.array(input_docs, dtype="int32")
input_tokens = np.array(input_tokens, dtype="int32")
outputs = np.array(outputs)

idx2token = dict([(i,t) for t,i in token2idx.items()])
json.dump(idx2token, open("current_model.vocab.json",'w')) # Save token index mapping

### Modeling
# Create model with given settings
model = init_model(n_docs, vocab_len, n_topics, l1_doc, l1_word, lr)

log = {}
log['p_Ndocs'] = n_docs
log['p_BS'] = batch_size
log['p_NSrate'] = ns_rate
log['p_Ntopics'] = n_topics
log['p_L1doc'] = l1_doc
log['p_L1word'] = l1_word
log['p_LR'] = lr

# Print parameter names and values
print('\t'.join([name for name in log if name[0] == 'p']))
print('\t'.join([str(log[name]) for name in log if name[0] == 'p']))

#callbacks = [ModelCheckpoint("current_model.h5", save_best_only=False),
#			 ]

def evaluate_sparsity(docvecs):
	print("Doc-topic distribution sparsity")
	print("\tL2/L1\t>2/N") # Todo: Normalized Above 2/N measure: |{x|x>2/N}|/N
	doc_sparsity = sparsity(docvecs, n=1000)
	doc_peakiness = peak_rate(docvecs, 2., n=1000) # Interpretable measure of sparsity: (number of dimensions > 2/n_dims)/n_dims
	print("\t%.3f\t%.3f" % (doc_sparsity, doc_peakiness))
	return doc_sparsity, doc_peakiness

#def eval_

for epoch in range(0, n_epochs):
	hist = model.fit([input_docs, input_tokens], [outputs], batch_size=batch_size, verbose=1, epochs=epoch+1, initial_epoch=epoch)
	#if epoch % 3 != 2:
	#	continue
	model.save("current_model.h5")
	docvecs = get_docvecs(model)
	wordvecs = get_wordvecs(model, min_zero=False)
	# Evaluate
	log['a_Epoch'] = epoch
	log['m_DocL2L1'], log['m_DocPeak'] = evaluate_sparsity(docvecs)
	topic_words = get_topic_words(wordvecs)
	log['m_tOverlap'] = topic_overlap(topic_words)
	log['m_tPrec'],	log['m_tRecall'] = topic_prec_recall(topic_words, idx2token, cntr, stopidxs=set(), n_freq_words=n_topics*10)#[token2idx[w] for w in stopwords])
	log['m_tWordy'], log['m_tStopy'] = topic_wordiness(topic_words, idx2token), topic_stopwordiness(topic_words, idx2token, stopwords_fi_lemma)
	log['z_F1'], log['z_Loss'] = hist.history['fmeasure'][0], hist.history['loss'][0]
	coherences = []
	print("\nTopic words")
	for topic in topic_words:
		coherences.append(pmix_coherence([idx2token[i] for _, i in topic_words[topic]], cntr, cocntr, blacklist=stopwords_fi_lemma))
		print("%d (%.3f):" % (topic, coherences[-1]), ', '.join(["%s" % idx2token[word_id] for score, word_id in topic_words[topic]]))
	log['m_PMI'] = np.nanmean(coherences)
	print("Mean semantic coherence: %.3f" % log['m_PMI'])
	write_log(log, "log_stt_coh4.csv")



# Print topic words
print("Topic overlap:", log['m_tOverlap']) # Topic-topic overlap in top-10 words; good range: 0-0.15
print("Topic precision:", log['m_tPrec'])
print("Topic recall:",	log['m_tRecall']) # Topic recall: how well top-10 topic words cover top-10*n_topics most frequent words; good range: 0.3-1
print("Topic wordiness:",	log['m_tWordy']) # Rate of alpha tokens (i.e., good topic words compared to numbers and punctuations); good range: 0.94-1
print("Topic stop wordiness:",	log['m_tStopy']) # Rate of stop words; good range: 0-0.05


### Inspect and evaluate topic model (obsolete stuff)
# Plot first document's topic distribution
#plt.plot(L1normalize(docvecs[0]), "C0", alpha=1)
#plt.show(block=False)

"""
stopwords = set("the a of to that for and an in is from on or be by as are with may at".split())
stopwords = set()
topic_words = get_topic_words(stopwords=stopwords)
"""
"""
print("Topic quality")
print("\tOverlap\tPrec.\tRecall")
log['02_Overlap'] = topic_overlap(wordvecs, topic_words)
print("\t%.4f" % log['02_Overlap'], end="")
log['03_Prec'], log['04_Recall'] = topic_prec_recall(wordvecs, topic_words, cntr, stopidxs=[token2idx[w] for w in stopwords])
print("\t%.4f\t%.4f" % (log['03_Prec'], log['04_Recall']))
"""
#mean_topicdist = L1normalize(sum(docvecs)/len(docvecs))
#print("Doc-topic weight mean: %.4f std %.4f" % (np.mean(mean_topicdist), np.std(mean_topicdist)))
#log['05_DocStd'] = np.std(mean_topicdist)
#log['06_F1'] = f1s[-1]


#log['07_coherence'] = topic_coherence(topic_words, idx2token)

#most_similar_words(wordvecs, 'payment')
#most_similar_words(wordvecs, 'poliisi')
