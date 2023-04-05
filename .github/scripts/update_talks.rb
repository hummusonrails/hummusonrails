require 'httparty'
require 'nokogiri'
require 'octokit'

# Scrape talks from the website
url = "https://www.bengreenberg.dev/talks/"
response = HTTParty.get(url)
parsed_page = Nokogiri::HTML(response.body)
talks = parsed_page.css('tbody tr')

# Generate the updated talks list
talks_list = ["Some recent talks I've given at conferences include:\n"]
talks.first(5).each do |talk|
  presentation = talk.css('div.text-sm.font-medium.text-gray-900').text.strip
  conference = talk.css('td:nth-child(2) div.text-sm.text-gray-900').text.strip
  watch_link = talk.at_css('a')[:href]
  talks_list << "* [#{presentation}](#{watch_link}) at #{conference}"
end

# Update the README.md file
client = Octokit::Client.new(access_token: ENV['GITHUB_TOKEN'])
repo = ENV['GITHUB_REPOSITORY']
readme = client.readme(repo)
readme_content = Base64.decode64(readme[:content]).force_encoding('UTF-8')

updated_content = readme_content.sub(/(?<=Some recent talks I've given at conferences include:)[\s\S]*?(?=\n\n)/, "\n" + talks_list.join("\n"))

client.update_contents(repo, 'README.md', 'Update recent talks', readme[:sha], updated_content)
